# PHP / Laravel — PSP integration code

Rules and the failures they prevent: `references/integration-patterns.md`. When the project outgrows
the baseline (several instances, real concurrency on one order, crash-during-POST, sweep jobs):
`references/hardening-concurrency.md`. The code below is the **baseline** level, PHP 8.3 /
Laravel 11 on PostgreSQL (the SQL relies on `ON CONFLICT ... RETURNING` and partial unique indexes).

**Adapt, don't transplant:** reuse the project's HTTP client, models, logger, config and test
framework. Field names, endpoints and states are fixed by the API; everything else is yours. Each
block starts with `<?php // path` — the file it lives in.

## 1. PSP client

```php
<?php // config/stream.php — env() is read HERE only: after `php artisan config:cache`, env() called
//                         anywhere else returns null, and the client would send "Bearer ".
return [
    'api_url'     => env('STREAM_API_URL'),
    'api_key'     => env('STREAM_API_KEY'),       // never log it, never send it to the browser
    'signing_key' => env('STREAM_SIGNING_KEY'),
    'timeout_ms'  => (int) env('STREAM_TIMEOUT_MS', 30000),
];
```

```php
<?php // app/Providers/AppServiceProvider.php
namespace App\Providers;

use App\Psp\PspClient;
use App\Psp\SignatureVerifier;
use Illuminate\Support\ServiceProvider;
use RuntimeException;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->app->singleton(PspClient::class, fn () => new PspClient(
            self::required('stream.api_url'), self::required('stream.api_key'),
            timeout: config('stream.timeout_ms') / 1000));
        $this->app->singleton(SignatureVerifier::class,
            fn () => new SignatureVerifier(self::required('stream.signing_key')));
    }

    public function boot(): void
    {   // Fail fast at boot, not on the first payment: an empty key is a 401, an empty signing key
        foreach (['stream.api_url', 'stream.api_key', 'stream.signing_key'] as $key) {   // fails
            self::required($key);                                                   // every webhook
        }
    }

    private static function required(string $key): string
    {
        $value = config($key);
        if (! is_string($value) || $value === '') {
            throw new RuntimeException("{$key} is not set");
        }
        return $value;
    }
}
```

```php
<?php // app/Psp/PaymentResult.php
namespace App\Psp;

use UnexpectedValueException;

/** Documented state enum: CHECKOUT | PENDING | AWAITING_APPROVAL | AUTHORIZED | COMPLETED | DECLINED
 *  | CANCELLED. `state` is deliberately a plain string, not a backed enum: State::from('ADDED_IN_v2')
 *  throws ValueError, and a state the API adds later must reach the transition whitelist (which
 *  ignores what it does not know) — in the webhook a ValueError is a 500 the PSP redelivers forever. */
final readonly class PaymentResult
{
    public function __construct(
        public string $id,
        public string $state,
        public ?string $referenceId = null,
        public ?string $paymentType = null,      // DEPOSIT | WITHDRAWAL | REFUND
        public ?string $amount = null,           // decimal MAJOR units as a STRING: '10.01' == 10.01 GBP
        public ?string $currency = null,
        public ?string $redirectUrl = null,
        public ?string $parentPaymentId = null,
        public ?string $errorCode = null,
        public ?string $errorMessage = null,
    ) {}

    /** The API sends many more fields, all optional: unknown keys are ignored. */
    public static function fromArray(mixed $p): self
    {
        if (! is_array($p) || ! is_string($p['id'] ?? null) || $p['id'] === ''
            || ! is_string($p['state'] ?? null)) {
            throw new UnexpectedValueException('not a PaymentResult');
        }
        $str = static fn (string $k): ?string => is_scalar($p[$k] ?? null) ? (string) $p[$k] : null;
        return new self($p['id'], $p['state'], $str('referenceId'), $str('paymentType'),
            self::decimal($p['amount'] ?? null), $str('currency'), $str('redirectUrl'),
            $str('parentPaymentId'), $str('errorCode'), $str('errorMessage'));
    }

    /** json_decode() makes 10.01 a float. json_encode() prints the SHORTEST string that round-trips
     *  (serialize_precision = -1, the default) — the literal the PSP sent; `(string) $float` uses
     *  `precision` (14 significant digits) and rounds silently. The string goes to a numeric column
     *  and is compared in SQL or with bcmath, never as a float. */
    private static function decimal(mixed $v): ?string
    {
        return match (true) {
            $v === null => null,
            is_int($v) => (string) $v,
            is_float($v) => json_encode($v),
            is_string($v) && is_numeric($v) => $v,
            default => throw new UnexpectedValueException('amount is not a number'),
        };
    }
}
```

```php
<?php // app/Psp/PspTimeout.php
namespace App\Psp;

/** The call did not complete: the outcome is UNKNOWN — never treat it as a failure. */
final class PspTimeout extends \RuntimeException {}

<?php // app/Psp/PspApiError.php
namespace App\Psp;

final class PspApiError extends \RuntimeException
{
    public function __construct(public readonly int $status, public readonly mixed $body)
    {
        parent::__construct("PSP HTTP {$status}");
    }
}
```

```php
<?php // app/Psp/PspClient.php
namespace App\Psp;

use GuzzleHttp\Exception\TransferException;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;
use InvalidArgumentException;
use UnexpectedValueException;

final class PspClient
{
    public function __construct(
        private readonly string $baseUrl,
        private readonly string $apiKey,
        private readonly float $connectTimeout = 5.0,
        private readonly float $timeout = 30.0,   // TOTAL transfer time (cURL), not idle time
    ) {}

    /** A NEW PendingRequest per call: it is mutable, and a shared one leaks options across calls. */
    private function http(): PendingRequest
    {
        return Http::baseUrl(rtrim($this->baseUrl, '/'))
            ->withToken($this->apiKey)                   // never log the request or its options
            ->withUserAgent('psp-integration/1.0')       // some WL hosts (WAF) 403 the default UA
            ->acceptJson()->asJson()
            ->connectTimeout($this->connectTimeout)
            ->timeout($this->timeout);                   // and NO ->retry(): a re-sent POST is a
    }                                                    // second payment

    private function call(string $method, string $path, array $query = [], ?array $json = null): mixed
    {
        // Classify FAIL-SAFE: only a real HTTP status tells you what the PSP did; everything else
        // must become PspTimeout so it reaches the reconcile path. Laravel converts only Guzzle's
        // ConnectException into ConnectionException: refused, DNS, empty reply (cURL 52) AND
        // cURL 28, i.e. a READ timeout too. A reset mid-transfer (cURL 56), a broken
        // Content-Encoding (61) or too many redirects escape as a raw Guzzle TransferException,
        // which a `catch (ConnectionException)` alone misses — and a raw exception here leaves the
        // attempt claimed with nothing to resolve it. ->throw() is never called, so Laravel's own
        // RequestException (which carries a real status) cannot occur and nothing is mislabelled.
        try {
            $r = $method === 'GET' ? $this->http()->get($path, $query)
                                   : $this->http()->post($path, $json ?? []);
        } catch (ConnectionException|TransferException $e) {
            throw new PspTimeout("{$method} {$path}: ".class_basename($e), previous: $e);
        }
        if ($r->failed()) {                              // 4xx/5xx: the status is KNOWN
            throw new PspApiError($r->status(), $r->json() ?? mb_substr($r->body(), 0, 500));
        }
        $data = $r->json();                              // never throws: null for non-JSON (a proxy
        if (! is_array($data) || ! array_key_exists('result', $data)) {     // answering 200 HTML)
            throw new PspTimeout("{$method} {$path}: undecodable {$r->status()} body");
        }
        return $data['result'];                          // envelope: {timestamp, status, result}
    }

    /** Validate INSIDE the fail-safe boundary: the request was already sent, so a rejected body is
     *  an unknown outcome, not a bug to bubble up as UnexpectedValueException. */
    private static function one(mixed $payload): PaymentResult
    {
        try {
            return PaymentResult::fromArray($payload);
        } catch (UnexpectedValueException $e) {
            throw new PspTimeout('undecodable payment body', previous: $e);
        }
    }

    /** The API takes a JSON NUMBER in major units, so the float exists only on the wire. json_encode
     *  prints '10.01' as 10.01 only while serialize_precision = -1 (the default; a legacy php.ini
     *  with 17 sends 10.0099999999999997868) — the test checks the bytes. Public: callers validate
     *  BEFORE claiming an attempt, because an exception after the claim and before the send leaves
     *  an IN_FLIGHT attempt that no reconciliation will ever find. */
    public static function wireAmount(string $amount): float
    {
        if (! preg_match('/^\d{1,10}(\.\d{1,5})?$/', $amount)) {
            throw new InvalidArgumentException("invalid amount {$amount}");
        }
        return (float) $amount;
    }

    public function createDeposit(string $amount, string $currency, string $referenceId,
        string $returnUrl, string $webhookUrl, ?array $customer = null,
        ?array $billingAddress = null): PaymentResult
    {
        return self::one($this->call('POST', '/api/v1/payments', json: array_filter([
            'paymentType' => 'DEPOSIT', 'amount' => self::wireAmount($amount),
            'currency' => $currency, 'referenceId' => $referenceId, 'returnUrl' => $returnUrl,
            'webhookUrl' => $webhookUrl, 'customer' => $customer,
            'billingAddress' => $billingAddress], fn ($v) => $v !== null)));
    }

    public function createRefund(string $parentPaymentId, string $amount, string $currency,
        string $referenceId): PaymentResult
    {   // No refund endpoint: a REFUND is a new payment linked via parentPaymentId.
        return self::one($this->call('POST', '/api/v1/payments', json: [
            'paymentType' => 'REFUND', 'parentPaymentId' => $parentPaymentId,
            'amount' => self::wireAmount($amount), 'currency' => $currency,
            'referenceId' => $referenceId]));
    }

    public function getPayment(string $paymentId): PaymentResult
    {
        return self::one($this->call('GET', '/api/v1/payments/'.rawurlencode($paymentId)));
    }

    /** @return list<PaymentResult> — reconciliation */
    public function findByReferenceId(string $referenceId): array
    {
        $result = $this->call('GET', '/api/v1/payments', ['referenceId.eq' => $referenceId]) ?? [];
        // `?? []`: an empty match can arrive as null. Anything else that is not a list is UNKNOWN —
        // on the ONE path that must never fail unclassified.
        if (! is_array($result) || ! array_is_list($result)) {
            throw new PspTimeout('undecodable reconciliation body');
        }
        return array_map(self::one(...), $result);
    }
}
```

## 2. Webhook endpoint — the raw-body recipe

```php
<?php // app/Psp/SignatureVerifier.php
namespace App\Psp;

final class SignatureVerifier
{
    public function __construct(private readonly string $signingKey) {}

    public function valid(string $rawBody, ?string $header): bool
    {
        if ($header === null || $header === '') {
            return false;
        }
        // The Signature header is the LOWERCASE HEX digest (authentication.md §3); hash_hmac()
        // returns exactly that. Hex only — a base64 digest must NOT verify. hash_equals = constant
        // time, byte-wise (any header is safe to compare), and false on a length mismatch.
        return hash_equals(hash_hmac('sha256', $rawBody, $this->signingKey),
                           strtolower(trim($header)));
    }
}
```

```php
<?php // app/Http/Controllers/PspWebhookController.php
namespace App\Http\Controllers;

use App\Orders\ApplyWebhook;
use App\Psp\PaymentResult;
use App\Psp\SignatureVerifier;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;

final class PspWebhookController
{
    public function __invoke(Request $request, SignatureVerifier $signature,
                             ApplyWebhook $apply): JsonResponse
    {
        // RAW bytes. Not $request->all() / ->json() / a FormRequest: those are the DECODED body, and
        // json_encode() of it never reproduces the signed bytes (slashes, unicode, key order,
        // 10.0 -> 10). The global TrimStrings / ConvertEmptyStringsToNull middleware rewrite only
        // that decoded input bag; getContent() is untouched.
        $raw = $request->getContent();
        if (! $signature->valid($raw, $request->header('Signature'))) {
            Log::warning('psp webhook signature mismatch', ['bytes' => strlen($raw)]);  // no key
            return response()->json(['error' => 'invalid signature'], 401);
        }
        // Parse only after verifying. A verified body that is not a payment throws -> 500: nothing
        // was committed, so a redelivery is not lost.
        $event = PaymentResult::fromArray(json_decode($raw, true, flags: JSON_THROW_ON_ERROR));
        $outcome = $apply($event);
        Log::info('psp webhook', ['id' => $event->id, 'state' => $event->state, 'outcome' => $outcome]);
        return response()->json(['status' => $outcome]);            // always 2xx once verified
    }
}
```

```php
<?php // routes/api.php — prefixed /api: webhookUrl = https://shop.example/api/webhooks/psp
use App\Http\Controllers\PspWebhookController;
use Illuminate\Support\Facades\Route;

Route::post('/webhooks/psp', PspWebhookController::class);    // NOT inside auth / auth:sanctum
```

```php
<?php // bootstrap/app.php (Laravel 11)
use App\Orders\RetryLater;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        // THE classic Laravel pitfall: in routes/web.php the webhook runs through the `web` group —
        // session, cookies and VerifyCsrfToken — and every delivery gets 419 (the PSP has no CSRF
        // token). routes/api.php has none of that. A fresh Laravel 11 app has no api.php: add this
        // line by hand (`php artisan install:api` also works, but pulls in Sanctum you don't need).
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware) {
        // Only if the route must stay in routes/web.php instead — exclude it from CSRF explicitly:
        // $middleware->validateCsrfTokens(except: ['webhooks/psp']);
    })
    ->withExceptions(function (Exceptions $exceptions) {
        // Reconciliation ran and is still inconclusive: 409 + Retry-After, never a dead end.
        $exceptions->render(fn (RetryLater $e) => response()->json(
            ['error' => class_basename($e), 'reference' => $e->getMessage()], 409, ['Retry-After' => '5']));
    })->create();
```

## 3. Order state transition (idempotent, DB-guarded)

```php
<?php // database/migrations/2025_01_01_000000_create_psp_tables.php
use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        // ILLUSTRATIVE: map these columns onto the project's EXISTING orders table and statuses.
        Schema::create('orders', function (Blueprint $t) {
            $t->id();
            $t->string('order_ref')->unique();
            $t->string('status', 32)->default('AWAITING_PAYMENT');
            $t->decimal('paid_amount', 18, 5)->nullable();     // numeric: never a float column
            $t->string('paid_currency', 3)->nullable();
            $t->decimal('refunded_amount', 18, 5)->default(0);
            $t->string('psp_payment_id')->nullable()->index();
            $t->string('error_code')->nullable();
            $t->text('error_message')->nullable();
            $t->timestampTz('updated_at')->nullable();
        });
        Schema::create('psp_attempt', function (Blueprint $t) {
            $t->id();
            $t->foreignId('order_id')->constrained('orders');
            $t->string('reference_id')->unique();
            $t->string('psp_payment_id')->nullable();
            $t->text('redirect_url')->nullable();
            $t->string('state', 16);                          // IN_FLIGHT | READY | FAILED
            $t->timestampTz('created_at')->useCurrent();
        });
        Schema::create('psp_refund_attempt', function (Blueprint $t) {
            $t->id();
            $t->foreignId('order_id')->constrained('orders');
            $t->string('refund_key');
            $t->string('reference_id')->unique();
            $t->decimal('amount', 18, 5);
            $t->string('currency', 3);
            $t->string('psp_payment_id')->nullable();
            $t->string('state', 16);                          // IN_FLIGHT | DONE | FAILED
            $t->timestampTz('created_at')->useCurrent();
            $t->unique(['order_id', 'refund_key'], 'psp_refund_key');
        });
        Schema::create('psp_webhook_event', function (Blueprint $t) {   // INBOX, not tombstone
            $t->id();
            $t->string('payment_id');
            $t->string('state', 32);
            $t->timestampTz('received_at')->useCurrent();
            $t->timestampTz('processed_at')->nullable();
            $t->string('processed_reason', 16)->nullable();   // applied | duplicate
            $t->unique(['payment_id', 'state'], 'psp_webhook_once');
        });
        // The schema builder has no partial indexes: raw SQL. FAILED stays OUT of the open-attempt
        // index on purpose, so a refusal frees the order for a new attempt.
        DB::statement("create unique index psp_attempt_open on psp_attempt (order_id)
                       where state in ('IN_FLIGHT', 'READY')");
        DB::statement('create index psp_webhook_open on psp_webhook_event (received_at)
                       where processed_at is null');
    }

    public function down(): void
    {
        foreach (['psp_webhook_event', 'psp_refund_attempt', 'psp_attempt', 'orders'] as $t) {
            Schema::dropIfExists($t);
        }
    }
};
```

All SQL below goes through `DB::select*(..., useReadPdo: false)`: an `INSERT/UPDATE ... RETURNING`
is a *select* to Laravel, and with a read/write split it would otherwise run on the replica
outside a transaction (where it fails, or — worse — reads a stale row).

```php
<?php // app/Orders/ApplyWebhook.php
namespace App\Orders;

use App\Jobs\FulfilOrder;                   // YOUR heavy post-payment work: e-mails, ledger, …
use App\Psp\PaymentResult;
use Illuminate\Support\Facades\DB;

final class ApplyWebhook
{
    private const STATE_MAP = ['COMPLETED' => 'PAID', 'AUTHORIZED' => 'AUTHORIZED',   // whitelist:
        'DECLINED' => 'PAYMENT_FAILED', 'CANCELLED' => 'PAYMENT_FAILED'];   // integration-patterns.md
    private const ALLOWED_FROM = [
        'PAID' => ['AWAITING_PAYMENT', 'PROCESSING', 'AUTHORIZED'],
        'AUTHORIZED' => ['AWAITING_PAYMENT', 'PROCESSING'],
        'PAYMENT_FAILED' => ['AWAITING_PAYMENT', 'PROCESSING', 'AUTHORIZED'],
    ];

    /** @return 'applied'|'duplicate'|'unknown'|'ignored' */
    public function __invoke(PaymentResult $e): string
    {
        $next = self::STATE_MAP[$e->state] ?? null;
        if ($next === null) {
            return 'ignored';                       // non-final / unknown state: change nothing
        }
        return DB::transaction(function () use ($e, $next): string {   // commits on return
            // INBOX claim, not a tombstone: `do update ... where processed_at is null` takes over
            // (and row-locks) a receipt that was recorded but never applied, so concurrent
            // redeliveries serialise here; no row back = already applied = a real duplicate.
            $claim = DB::selectOne(
                'insert into psp_webhook_event (payment_id, state, received_at) values (?, ?, now())
                 on conflict (payment_id, state) do update set received_at = now()
                   where psp_webhook_event.processed_at is null
                 returning id', [$e->id, $e->state], false);
            if ($claim === null) {
                return 'duplicate';
            }
            // Conditional UPDATE: re-application and any downgrade of a final status match 0 rows.
            // Book amount/currency FROM THE PAYLOAD (the final amount may differ from the request).
            $allowed = self::ALLOWED_FROM[$next];
            $row = DB::selectOne(
                'update orders set status = ?, psp_payment_id = ?, updated_at = now(),
                        paid_amount = coalesce(?, paid_amount),
                        paid_currency = coalesce(?, paid_currency), error_code = ?, error_message = ?
                  where (psp_payment_id = ? or order_ref = ?)
                    and status in ('.implode(',', array_fill(0, count($allowed), '?')).')
                 returning id',
                [$next, $e->id, $e->amount, $e->currency, $e->errorCode, $e->errorMessage,
                 $e->id, $e->referenceId, ...$allowed], false);
            if ($row === null) {
                $known = DB::selectOne('select 1 as found from orders
                                         where psp_payment_id = ? or order_ref = ?',
                                       [$e->id, $e->referenceId], false);
                // The webhook can beat the create-payment response. COMMIT the receipt but leave
                // processed_at NULL: marking it processed here loses the event forever, because the
                // redelivery would be dismissed as a duplicate and the order never transitions.
                if ($known === null) {
                    return 'unknown';               // ack with 200 so the PSP stops redelivering
                }
            }
            // 0 rows with a known order = already past this transition: settled either way.
            DB::update('update psp_webhook_event set processed_at = now(), processed_reason = ?
                         where id = ?', [$row === null ? 'duplicate' : 'applied', $claim->id]);
            if ($row === null) {
                return 'duplicate';
            }
            if ($next === 'PAID') {
                // afterCommit(): never runs before the order is PAID, whatever the queue driver. A
                // push failing after the commit leaves the order PAID but unfulfilled (the Python
                // baseline's gap); with QUEUE_CONNECTION=database on THIS connection, drop
                // afterCommit() and the jobs row commits/rolls back with the receipt — an outbox.
                FulfilOrder::dispatch((int) $row->id)->afterCommit();
            }
            return 'applied';
        });
    }
}
```

## 4. Creation and refund idempotency

```php
<?php // app/Orders/RetryLater.php
namespace App\Orders;

/** -> HTTP 409 + Retry-After (bootstrap/app.php). */
abstract class RetryLater extends \RuntimeException {}

<?php // app/Orders/CheckoutInProgress.php
namespace App\Orders;

/** The attempt state is churning. */
final class CheckoutInProgress extends RetryLater {}

<?php // app/Orders/PaymentOutcomeUnknown.php
namespace App\Orders;

/** Reconciliation ran and is STILL inconclusive. The attempt stays claimed, so the next call
 *  reconciles again: never a dead end. */
final class PaymentOutcomeUnknown extends RetryLater {}

<?php // app/Orders/RefundOutcomeUnknown.php
namespace App\Orders;

/** A committed refund attempt is unresolved: reconcile it, never start a new one. */
final class RefundOutcomeUnknown extends RetryLater {}

<?php // app/Orders/CheckoutFailed.php
namespace App\Orders;

/** The attempt is FAILED and the order is free again: the client may start a NEW checkout. */
final class CheckoutFailed extends \RuntimeException {}

<?php // app/Orders/RefundFailed.php
namespace App\Orders;

/** This refund key was refused and its reservation released: a corrected refund needs a NEW key. */
final class RefundFailed extends \RuntimeException {}
```

```php
<?php // app/Orders/Checkout.php
namespace App\Orders;

use App\Psp\PaymentResult;
use App\Psp\PspApiError;
use App\Psp\PspClient;
use App\Psp\PspTimeout;
use DomainException;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Str;
use LogicException;

final class Checkout
{
    private const ATTEMPT = 'id, order_id, reference_id, state, redirect_url';
    private const ACTIVE = "('IN_FLIGHT','READY')";     // FAILED excluded: never blocks a new attempt
    private const REFUND = 'id, order_id, reference_id, amount, state, psp_payment_id';

    public function __construct(private readonly PspClient $psp) {}

    /** Rule 2 needs a REAL commit before the PSP call. Inside an outer DB::transaction() (a
     *  controller or job wrapping the action, RefreshDatabase in tests) every DB::transaction()
     *  below is only a SAVEPOINT: nothing commits, and a timeout rolls back the referenceId you
     *  must reconcile with. Refuse loudly instead of degrading silently. */
    private static function outsideAnyTransaction(): void
    {
        if (DB::transactionLevel() > 0) {
            throw new LogicException('PSP calls must not run inside a DB transaction');
        }
    }

    /** Atomic get-or-create, COMMITTED before the PSP call. Returns [attempt, owner]: only the
     *  caller that INSERTED the row may POST. */
    private function claimAttempt(int $orderId): array
    {
        for ($pass = 0; $pass < 2; $pass++) {   // 2nd pass: the active attempt turned FAILED between
            $claimed = DB::transaction(function () use ($orderId) {          // the two statements
                $fresh = DB::selectOne('insert into psp_attempt (order_id, reference_id, state)
                         values (?, ?, \'IN_FLIGHT\')
                         on conflict (order_id) where state in '.self::ACTIVE.' do nothing
                         returning '.self::ATTEMPT,
                    [$orderId, "order-{$orderId}-".Str::uuid()], false);
                if ($fresh !== null) {
                    return [$fresh, true];
                }
                $active = DB::selectOne('select '.self::ATTEMPT.' from psp_attempt
                         where order_id = ? and state in '.self::ACTIVE, [$orderId], false);
                return $active === null ? null : [$active, false];
            });                                 // <- COMMIT: the referenceId is durable from here
            if ($claimed !== null) {
                return $claimed;
            }
        }
        throw new CheckoutInProgress("order {$orderId}");
    }

    public function startCheckout(int $orderId, string $amount, string $currency): ?string
    {
        self::outsideAnyTransaction();
        PspClient::wireAmount($amount);                 // validate BEFORE the claim (see there)
        [$attempt, $owner] = $this->claimAttempt($orderId);
        if (! $owner) {
            return $this->joinAttempt($attempt);        // a non-owner never POSTs, never mints a ref
        }
        try {                                           // no transaction is open across this call
            $payment = $this->psp->createDeposit($amount, $currency, $attempt->reference_id,
                returnUrl: 'https://shop.example/return/{id}/{referenceId}/{state}/{type}',
                webhookUrl: 'https://shop.example/api/webhooks/psp',
                customer: ['referenceId' => "customer_{$orderId}"]);
        } catch (PspApiError $e) {
            if ($e->status < 500) {
                $this->setAttemptState($attempt->id, 'FAILED');  // confirmed: nothing created,
                throw $e;                                        // so the order is free
            }
            return $this->resolveAttempt($attempt);     // ambiguous 5xx: the payment may exist
        } catch (PspTimeout) {
            return $this->resolveAttempt($attempt);     // unknown: reconcile, never a second POST
        }
        return $this->promoteReady($attempt, $payment);
    }

    /** READY -> the stored URL. Still IN_FLIGHT -> reconcile, so a 409 always follows real progress.
     *  (One extra GET per concurrent click; the cheaper UNKNOWN split: hardening-concurrency.md §1.) */
    private function joinAttempt(object $attempt): ?string
    {
        return $attempt->state === 'READY' ? $attempt->redirect_url
                                           : $this->resolveAttempt($attempt);
    }

    /** The only way out of an unresolved attempt: GET by the PERSISTED referenceId. */
    public function resolveAttempt(object $attempt): ?string
    {
        $found = $this->psp->findByReferenceId($attempt->reference_id)[0] ?? null;
        if ($found === null) {                          // stays claimed: retried by the next call
            throw new PaymentOutcomeUnknown($attempt->reference_id);
        }
        if (in_array($found->state, ['DECLINED', 'CANCELLED'], true)) {
            $this->setAttemptState($attempt->id, 'FAILED');   // frees the order for a NEW attempt
            throw new CheckoutFailed($found->errorCode ?? $found->state);
        }
        return $this->promoteReady($attempt, $found);
    }

    private function promoteReady(object $attempt, PaymentResult $payment): ?string
    {
        DB::transaction(function () use ($attempt, $payment) {
            DB::update("update psp_attempt set state = 'READY', psp_payment_id = ?, redirect_url = ?
                         where id = ?", [$payment->id, $payment->redirectUrl, $attempt->id]);
            DB::update("update orders set status = 'PROCESSING', psp_payment_id = ?
                         where id = ? and status = 'AWAITING_PAYMENT'",   // a webhook may have won
                       [$payment->id, $attempt->order_id]);
        });
        return $payment->redirectUrl;   // CHECKOUT, not paid; null once past checkout: poll the order
    }

    private function setAttemptState(int $attemptId, string $state): void
    {
        DB::update('update psp_attempt set state = ? where id = ?', [$state, $attemptId]);
    }

    /** Idempotent per (orderId, refundKey) — refundKey identifies ONE logical refund. ONLY the
     *  caller that INSERTED the attempt may POST: referenceId is NOT an idempotency key at the PSP,
     *  so a second POST for the same refund is a second payout. */
    public function refundOrder(int $orderId, string $refundKey, string $amount,
                                string $currency): PaymentResult
    {
        self::outsideAnyTransaction();
        PspClient::wireAmount($amount);
        [$attempt, $owner, $parentId] = $this->reserveRefund($orderId, $refundKey, $amount, $currency);
        if ($attempt->psp_payment_id !== null) {
            return $this->psp->getPayment($attempt->psp_payment_id);   // DONE/FAILED: pure replay
        }
        if ($attempt->state === 'FAILED') {             // refused, nothing created, amount released:
            throw new RefundFailed($refundKey);         // the same answer every time, not a 409
        }
        if (! $owner) {
            return $this->reconcileRefund($attempt);    // someone else's (or a crashed) attempt
        }
        try {
            $result = $this->psp->createRefund($parentId, $amount, $currency, $attempt->reference_id);
        } catch (PspApiError $e) {
            if ($e->status < 500) {
                // CONFIRMED refusal: this referenceId will never be found. Left IN_FLIGHT it would
                // 409 forever AND keep the amount reserved — give it back and close the attempt.
                $this->finishRefund($attempt, 'FAILED', null);
                throw $e;
            }
            return $this->reconcileRefund($attempt);    // 5xx: the refund may exist after all
        } catch (PspTimeout) {
            return $this->reconcileRefund($attempt);    // UNKNOWN: reconcile the COMMITTED row's ref
        }
        return $this->settleRefund($attempt, $result);
    }

    /** Returns [attempt, owner, parentPaymentId]. ONE commit for the attempt AND the amount
     *  reservation, before the PSP call. A row that already existed belongs to another call: its
     *  amount must NOT be reserved a second time and its caller must NOT POST. Public: tests use it. */
    public function reserveRefund(int $orderId, string $refundKey, string $amount,
                                  string $currency): array
    {
        return DB::transaction(function () use ($orderId, $refundKey, $amount, $currency) {
            $order = DB::selectOne('select psp_payment_id from orders where id = ?', [$orderId], false)
                ?? throw new DomainException('unknown order');
            $row = DB::selectOne('insert into psp_refund_attempt
                       (order_id, refund_key, amount, currency, reference_id, state)
                     values (?, ?, ?, ?, ?, \'IN_FLIGHT\')
                     on conflict (order_id, refund_key) do nothing returning '.self::REFUND,
                [$orderId, $refundKey, $amount, $currency, "refund-{$orderId}-".Str::uuid()], false);
            if ($row === null) {                        // exists: reuse ITS referenceId, reserve nothing
                $row = DB::selectOne('select '.self::REFUND.' from psp_refund_attempt
                         where order_id = ? and refund_key = ?', [$orderId, $refundKey], false);
                return [$row, false, $order->psp_payment_id];
            }
            $reserved = DB::update("update orders set refunded_amount = refunded_amount + ?
                         where id = ? and status = 'PAID'
                           and refunded_amount + ? <= paid_amount",   // refund only the remainder
                       [$amount, $orderId, $amount]);
            if ($reserved === 0) {                      // throws -> rolls back the attempt row too
                throw new DomainException('refund exceeds remaining refundable amount');
            }
            return [$row, true, $order->psp_payment_id];
        });                                             // <- COMMIT, before any PSP call
    }

    private function reconcileRefund(object $attempt): PaymentResult
    {   // GET by the PERSISTED referenceId. A fresh referenceId here is a SECOND payout.
        $found = $this->psp->findByReferenceId($attempt->reference_id)[0]
            ?? throw new RefundOutcomeUnknown($attempt->reference_id);
        return $this->settleRefund($attempt, $found);
    }

    private function settleRefund(object $attempt, PaymentResult $result): PaymentResult
    {
        $failed = in_array($result->state, ['DECLINED', 'CANCELLED'], true);
        $this->finishRefund($attempt, $failed ? 'FAILED' : 'DONE', $result->id);
        return $result;
    }

    private function finishRefund(object $attempt, string $state, ?string $paymentId): void
    {
        DB::transaction(function () use ($attempt, $state, $paymentId) {
            // State-conditional: the owner and a reconciler can settle the same attempt, and
            // releasing the reservation twice would inflate the refundable amount.
            $done = DB::update("update psp_refund_attempt set state = ?,
                                       psp_payment_id = coalesce(?, psp_payment_id)
                                 where id = ? and state = 'IN_FLIGHT'",
                               [$state, $paymentId, $attempt->id]);
            if ($state === 'FAILED' && $done === 1) {   // only a CONFIRMED failure gives it back
                DB::update('update orders set refunded_amount = refunded_amount - ? where id = ?',
                           [$attempt->amount, $attempt->order_id]);
            }
        });
    }
}
```

## 5. Tests (PHPUnit + Http::fake + a real PostgreSQL)

The guarantees rest on `ON CONFLICT`, partial indexes and **committed** transactions, so the tests
need a real PostgreSQL and **must not** use `RefreshDatabase`: it wraps every test in a transaction,
`DB::transactionLevel()` is then 1, nothing below ever commits (the guard in §4 refuses to run), and
a race test would serialise on one connection. `DatabaseTruncation` keeps commits real. The two
race tests fork with `pcntl` (CLI only), one fresh connection per child. `phpunit.xml` sets
`DB_CONNECTION=pgsql` and sandbox `STREAM_*` values — never production credentials.

```php
<?php // tests/PspFakes.php
namespace Tests;

use Closure;
use GuzzleHttp\Exception\ConnectException;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;

/** ONE Http::fake per test, routed through a table the test may swap. A second Http::fake() call
 *  APPENDS stubs and the first match wins, so "reset and re-stub" would silently keep the old one. */
trait PspFakes
{
    protected const BASE = 'https://sandbox.psp.invalid';   // == STREAM_API_URL in phpunit.xml
    protected const CHECKOUT_1 = ['id' => 'pay1', 'state' => 'CHECKOUT',
                                  'redirectUrl' => 'https://checkout.example/pay1'];
    protected const REFUND_5 = ['state' => 'COMPLETED', 'paymentType' => 'REFUND',
                                'amount' => 5.00, 'currency' => 'GBP'];
    /** @var array<string, mixed> 'METHOD /path-pattern' => response | Closure(Request) */
    protected array $psp = [];

    protected function fakePsp(): void
    {
        Http::preventStrayRequests();   // anything unrouted throws — and that is NOT a PspTimeout
        Http::fake(function (Request $r) {
            foreach ($this->psp as $route => $answer) {
                [$method, $path] = explode(' ', $route, 2);
                if ($r->method() === $method && Str::is(self::BASE.$path, $r->url())) {
                    return $answer instanceof Closure ? $answer($r) : $answer;
                }
            }
            return null;
        });
    }

    protected static function wrapped(mixed $result): mixed    // {timestamp, status, result}
    {
        return Http::response(['status' => 200, 'result' => $result]);
    }

    /** cURL 28 as Guzzle raises it: a ConnectException — for a READ timeout too. */
    protected static function timeout(): Closure
    {
        return fn (Request $r) => throw new ConnectException(
            'cURL error 28: Operation timed out after 30001 milliseconds', $r->toPsrRequest());
    }

    protected function posts(): int
    {
        return count(Http::recorded(fn (Request $r) => $r->method() === 'POST'));
    }

    protected function order(int $id, string $status = 'AWAITING_PAYMENT', ?string $paid = null,
                             ?string $pspPaymentId = null): void
    {
        DB::table('orders')->insert(['id' => $id, 'order_ref' => "order-{$id}-a",
            'status' => $status, 'paid_amount' => $paid, 'paid_currency' => $paid ? 'GBP' : null,
            'psp_payment_id' => $pspPaymentId, 'updated_at' => now()]);
    }

    protected function fetchOrder(int $id): object
    {
        return DB::table('orders')->find($id);
    }
}
```

```php
<?php // tests/Feature/PspClientTest.php
namespace Tests\Feature;

use App\Psp\PaymentResult;
use App\Psp\PspClient;
use App\Psp\PspTimeout;
use App\Psp\SignatureVerifier;
use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Exception\RequestException;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\Http;
use Tests\PspFakes;
use Tests\TestCase;

final class PspClientTest extends TestCase
{
    use PspFakes;

    protected function setUp(): void
    {
        parent::setUp();
        $this->fakePsp();
    }

    private function deposit(string $base = self::BASE, float $timeout = 1.0): PaymentResult
    {
        return (new PspClient($base, 'sandbox-key', 1.0, $timeout))->createDeposit(
            '10.01', 'GBP', 'order-1-a', 'https://shop.example/r', 'https://shop.example/api/webhooks/psp');
    }

    public function test_the_signature_matches_the_published_test_vector(): void
    {
        $raw = '{"id":"9e9003d7f3324fc6828481c665d8bab5","paymentType":"DEPOSIT","state":"CANCELLED",'
             . '"amount":2000,"currency":"CLP","errorCode":"1.04","errorMessage":"Cancelled by Timeout"}';
        $sig = '6438a8077670ef1909ebcc1f9729e1c4c08d56a1ddeb02c928fbb1acab393f7b';
        $verifier = new SignatureVerifier('sk_test_51f0c3a9d2e84b7c');
        $this->assertTrue($verifier->valid($raw, $sig));
        $this->assertTrue($verifier->valid($raw, ' '.strtoupper($sig)."\n"));   // tolerated
        $this->assertFalse($verifier->valid($raw."\n", $sig));                   // one byte more
        $this->assertFalse($verifier->valid($raw, base64_encode(hex2bin($sig)))); // right digest,
        $this->assertFalse($verifier->valid($raw, null));                         // wrong encoding
    }

    public function test_successful_deposit(): void
    {
        $this->psp = ['POST /api/v1/payments' => self::wrapped(self::CHECKOUT_1)];
        $p = $this->deposit();
        $this->assertSame(['CHECKOUT', self::CHECKOUT_1['redirectUrl']],   // created, NOT paid
                          [$p->state, $p->redirectUrl]);
        Http::assertSent(fn (Request $r) => str_contains($r->body(), '"amount":10.01,')  // a JSON
            && $r['paymentType'] === 'DEPOSIT'                                // number, exact
            && $r->hasHeader('Authorization', 'Bearer sandbox-key'));
    }

    public function test_decline_is_http_200_with_declined_state(): void
    {
        $this->psp = ['GET /api/v1/payments/pay2' => self::wrapped(
            ['id' => 'pay2', 'state' => 'DECLINED', 'errorCode' => '4.01'])];
        $p = (new PspClient(self::BASE, 'k'))->getPayment('pay2');
        $this->assertSame(['DECLINED', '4.01'], [$p->state, $p->errorCode]);
    }

    public function test_timeout_means_unknown_outcome(): void
    {
        $this->psp = ['POST /api/v1/payments' => self::timeout()];
        $this->expectException(PspTimeout::class);
        $this->deposit();
    }

    public function test_every_ending_without_a_status_is_unknown_never_a_raw_exception(): void
    {
        // All of these happen AFTER the request may have been processed and none carries a usable
        // status. Each must be PspTimeout: a raw exception escapes startCheckout's catch clauses and
        // leaves the attempt claimed with nothing to resolve it.
        $endings = [
            'refused (cURL 7)' => fn (Request $r) => throw new ConnectException(
                'cURL error 7: Failed to connect', $r->toPsrRequest()),
            'reset after send (cURL 56)' => fn (Request $r) => throw new RequestException(
                'cURL error 56: Connection reset by peer', $r->toPsrRequest()),
            'broken Content-Encoding (cURL 61)' => fn (Request $r) => throw new RequestException(
                'cURL error 61: Unrecognized content encoding', $r->toPsrRequest()),
            'proxy HTML' => Http::response('<html>proxy</html>', 200),
            'not the envelope' => Http::response([], 200),
            'no id' => self::wrapped(['state' => 'CHECKOUT']),
            'result: null' => self::wrapped(null),
        ];
        foreach ($endings as $name => $ending) {
            $this->psp = ['POST /api/v1/payments' => $ending];
            try {
                $this->deposit();
                $this->fail("{$name}: no exception");
            } catch (PspTimeout) {
                $this->addToAssertionCount(1);
            }
        }
        $this->psp = ['GET /api/v1/payments/pay8' => self::wrapped(['id' => 'pay8', 'state' => 'ADDED_IN_v2'])];
        $this->assertSame('ADDED_IN_v2', (new PspClient(self::BASE, 'k'))->getPayment('pay8')->state);
        // ^ a state the API added later is accepted and left to the whitelist, not rejected
    }

    /** The fakes above prove the mapping per exception CLASS; this proves real cURL raises them. */
    public function test_real_sockets_timeout_refused_empty_reply_and_bad_encoding_are_unknown(): void
    {
        Http::preventStrayRequests(false);          // unrouted -> these really go out
        $cause = function (string $base, float $timeout = 1.0): \Throwable {
            try {
                $this->deposit($base, $timeout);
            } catch (PspTimeout $e) {
                return $e->getPrevious();
            }
            $this->fail("{$base}: no PspTimeout");
        };
        // Listens, never accepts: the kernel completes the handshake, so this is a READ timeout.
        $silent = stream_socket_server('tcp://127.0.0.1:0');
        $started = microtime(true);
        $this->assertInstanceOf(ConnectionException::class,
            $cause('http://'.stream_socket_get_name($silent, false), 0.3));
        $this->assertLessThan(2.0, microtime(true) - $started);    // the timeout is really set
        // Nothing listening: refused.
        $closed = stream_socket_server('tcp://127.0.0.1:0');
        $addr = stream_socket_get_name($closed, false);
        fclose($closed);
        $this->assertInstanceOf(ConnectionException::class, $cause("http://{$addr}"));
        // 1st connection: read the request, close without answering (cURL 52). 2nd: answer 200 with
        // a body that claims gzip and is not (cURL 61) — the request was processed, yet Laravel
        // passes this one through as a RAW Guzzle RequestException.
        $server = proc_open([PHP_BINARY, '-r', '$s = stream_socket_server("tcp://127.0.0.1:0");
            echo stream_socket_get_name($s, false), "\n";
            $c = stream_socket_accept($s, 5); fread($c, 65536); fclose($c);
            $c = stream_socket_accept($s, 5); fread($c, 65536); fwrite($c, "HTTP/1.1 200 OK\r\n"
              . "Content-Type: application/json\r\nContent-Encoding: gzip\r\nContent-Length: 9\r\n"
              . "Connection: close\r\n\r\nnot-gzip!"); fclose($c);'], [1 => ['pipe', 'w']], $pipes);
        $addr = 'http://'.trim(fgets($pipes[1]));
        $this->assertInstanceOf(ConnectionException::class, $cause($addr));      // empty reply
        $badEncoding = $cause($addr);
        proc_close($server);
        $this->assertInstanceOf(RequestException::class, $badEncoding);
        $this->assertNotInstanceOf(ConnectException::class, $badEncoding);   // what a catch of
    }                                                                       // ConnectionException misses
}
```

```php
<?php // tests/Feature/PspWebhookTest.php
namespace Tests\Feature;

use App\Jobs\FulfilOrder;
use App\Psp\SignatureVerifier;
use Illuminate\Foundation\Testing\DatabaseTruncation;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Queue;
use Illuminate\Testing\TestResponse;
use Tests\PspFakes;
use Tests\TestCase;

final class PspWebhookTest extends TestCase
{
    use DatabaseTruncation, PspFakes;

    private const KEY = 'sk_test_51f0c3a9d2e84b7c';
    private const BODY = '{"id":"pay1","referenceId":"order-1-a","state":"COMPLETED","amount":10.01,"currency":"GBP"}';

    protected function setUp(): void
    {
        parent::setUp();
        config(['stream.signing_key' => self::KEY]);
        $this->app->forgetInstance(SignatureVerifier::class);
        Queue::fake();
        $this->order(1);
    }

    private static function sign(string $body, bool $base64 = false): string
    {
        $mac = hash_hmac('sha256', $body, self::KEY, binary: true);
        return $base64 ? base64_encode($mac) : bin2hex($mac);
    }

    /** RAW bytes: postJson() re-encodes an array — exactly the bug the signature check catches. */
    private function send(string $body, ?string $signature = null): TestResponse
    {
        return $this->call('POST', '/api/webhooks/psp', server: ['CONTENT_TYPE' => 'application/json']
            + ($signature === null ? [] : ['HTTP_SIGNATURE' => $signature]), content: $body);
    }

    public function test_the_published_test_vector_through_the_route(): void
    {
        $raw = '{"id":"9e9003d7f3324fc6828481c665d8bab5","paymentType":"DEPOSIT","state":"CANCELLED",'
             . '"amount":2000,"currency":"CLP","errorCode":"1.04","errorMessage":"Cancelled by Timeout"}';
        $sig = '6438a8077670ef1909ebcc1f9729e1c4c08d56a1ddeb02c928fbb1acab393f7b';
        $this->send($raw, $sig)->assertOk()->assertJson(['status' => 'unknown']);  // no such order
        $this->send(str_replace('2000', '2001', $raw), $sig)->assertUnauthorized();  // tampered
        $this->send($raw)->assertUnauthorized();                                    // no header
        $this->send($raw, base64_encode(hex2bin($sig)))->assertUnauthorized();       // base64
    }

    public function test_valid_webhook_marks_order_paid(): void
    {
        $this->send(self::BODY, self::sign(self::BODY))->assertOk()->assertJson(['status' => 'applied']);
        $order = $this->fetchOrder(1);
        $this->assertSame('PAID', $order->status);
        $this->assertSame(0, bccomp($order->paid_amount, '10.01', 5));  // payload, not the request
        Queue::assertPushed(FulfilOrder::class, 1);
    }

    public function test_invalid_signature_rejected_and_order_untouched(): void
    {
        $this->send(self::BODY, 'deadbeef')->assertUnauthorized();
        $this->assertSame('AWAITING_PAYMENT', $this->fetchOrder(1)->status);
    }

    public function test_base64_encoded_digest_is_rejected(): void
    {   // The right digest in the wrong encoding: the header is hex, so this must not verify.
        $this->send(self::BODY, self::sign(self::BODY, base64: true))->assertUnauthorized();
        $this->assertSame('AWAITING_PAYMENT', $this->fetchOrder(1)->status);
        $this->assertSame(0, DB::table('psp_webhook_event')->count());   // nothing was recorded
    }

    public function test_duplicate_webhook_is_a_no_op(): void
    {
        $this->send(self::BODY, self::sign(self::BODY))->assertJson(['status' => 'applied']);
        $before = $this->fetchOrder(1);
        $this->send(self::BODY, self::sign(self::BODY))->assertOk()->assertJson(['status' => 'duplicate']);
        $this->assertEquals($before, $this->fetchOrder(1));
        $this->assertSame(1, DB::table('psp_webhook_event')->where('payment_id', 'pay1')->count());
        Queue::assertPushed(FulfilOrder::class, 1);                      // fulfilled once
    }

    public function test_webhook_before_the_order_link_is_not_swallowed(): void
    {
        // referenceId is the ATTEMPT's reference (not order_ref) and psp_payment_id is not stored
        // yet: the real create-payment/webhook race. The receipt must stay unprocessed.
        $early = '{"id":"pay7","referenceId":"order-1-9f2c","state":"COMPLETED","amount":10.01,"currency":"GBP"}';
        $this->send($early, self::sign($early))->assertOk()->assertJson(['status' => 'unknown']);
        $this->assertNull(DB::table('psp_webhook_event')->where('payment_id', 'pay7')->value('processed_at'));
        DB::table('orders')->where('id', 1)->update(['psp_payment_id' => 'pay7']);  // lands late
        $this->send($early, self::sign($early))->assertOk()->assertJson(['status' => 'applied']);
        $this->assertSame('PAID', $this->fetchOrder(1)->status);      // the redelivery applies it
    }

    public function test_a_non_final_state_is_acked_and_ignored(): void
    {
        $pending = '{"id":"pay1","referenceId":"order-1-a","state":"PENDING"}';
        $this->send($pending, self::sign($pending))->assertOk()->assertJson(['status' => 'ignored']);
        $this->assertSame('AWAITING_PAYMENT', $this->fetchOrder(1)->status);
    }
}
```

```php
<?php // tests/Feature/CheckoutTest.php
namespace Tests\Feature;

use App\Orders\Checkout;
use App\Orders\PaymentOutcomeUnknown;
use App\Orders\RefundFailed;
use App\Orders\RefundOutcomeUnknown;
use App\Psp\PspApiError;
use Closure;
use Illuminate\Foundation\Testing\DatabaseTruncation;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Queue;
use LogicException;
use Tests\PspFakes;
use Tests\TestCase;

final class CheckoutTest extends TestCase
{
    use DatabaseTruncation, PspFakes;

    private const FIND = 'GET /api/v1/payments?referenceId.eq=*';
    private Checkout $checkout;

    protected function setUp(): void
    {
        parent::setUp();
        $this->fakePsp();
        Queue::fake();
        $this->order(1, 'PAID', '10.01', 'pay1');
        foreach ([2, 3, 4] as $id) {
            $this->order($id);
        }
        $this->checkout = $this->app->make(Checkout::class);
    }

    /** Two processes, one instant, one connection each. Returns [outcome, POSTs sent] per child. */
    private function race(Closure $body): array
    {
        $start = microtime(true) + 0.5;
        $children = [];
        for ($i = 0; $i < 2; $i++) {
            $file = tempnam(sys_get_temp_dir(), 'race');
            if (($pid = pcntl_fork()) === 0) {
                try {                                      // a child must NEVER return into PHPUnit
                    $inherited = DB::connection()->getPdo();   // the PARENT's socket: keep it alive
                    DB::purge();                               // so it is never closed here
                    usleep(max(0, (int) (($start - microtime(true)) * 1e6)));
                    try {
                        $out = $body();
                        $out = is_object($out) ? $out->id : $out;
                    } catch (\Throwable $e) {
                        $out = $e::class;
                    }
                    file_put_contents($file, json_encode([$out, $this->posts()]));
                } finally {
                    posix_kill(getmypid(), SIGKILL);       // skip PHPUnit's shutdown and destructors
                }
            }
            $children[$pid] = $file;
        }
        $results = [];
        foreach ($children as $pid => $file) {
            pcntl_waitpid($pid, $status);
            $results[] = json_decode(file_get_contents($file), true);
            unlink($file);
        }
        return $results;
    }

    public function test_concurrent_start_checkout_creates_exactly_one_payment(): void
    {
        $this->psp = [
            'POST /api/v1/payments' => function () {
                usleep(300_000);                           // hold the race window open
                return self::wrapped(self::CHECKOUT_1);
            },
            self::FIND => self::wrapped([]),               // the loser reconciles, finds nothing yet
        ];
        $outcomes = $this->race(fn () => $this->checkout->startCheckout(4, '10.01', 'GBP'));
        $this->assertSame(1, array_sum(array_column($outcomes, 1)));   // exactly ONE payment created
        foreach ($outcomes as [$outcome]) {                            // the loser: 409 or the URL
            $this->assertContains($outcome, [self::CHECKOUT_1['redirectUrl'], PaymentOutcomeUnknown::class]);
        }
        $this->assertSame(1, DB::table('psp_attempt')->where('order_id', 4)->count());  // ONE ref
    }

    // Baseline recovery path: no UNKNOWN state and no sweep job — the NEXT call reconciles.
    public function test_checkout_timeout_then_empty_reconciliation_recovers_later(): void
    {
        config(['database.connections.observer' => config('database.connections.pgsql')]);
        $seenByAnotherConnection = null;
        $this->psp = [
            'POST /api/v1/payments' => function ($r) use (&$seenByAnotherConnection) {
                $seenByAnotherConnection = DB::connection('observer')->table('psp_attempt')
                    ->where('order_id', 2)->value('state');   // visible elsewhere = COMMITTED
                return self::timeout()($r);
            },
            self::FIND => self::wrapped([]),                    // not visible at the PSP yet
        ];
        try {
            $this->checkout->startCheckout(2, '10.01', 'GBP');
            $this->fail('expected PaymentOutcomeUnknown');
        } catch (PaymentOutcomeUnknown) {
        }
        $this->assertSame('IN_FLIGHT', $seenByAnotherConnection);  // committed BEFORE the POST
        $stuck = DB::table('psp_attempt')->where('order_id', 2)->first();
        $this->assertSame('IN_FLIGHT', $stuck->state);             // still claimed, not a dead end
        $posts = $this->posts();
        $this->psp = ["GET /api/v1/payments?referenceId.eq={$stuck->reference_id}" => self::wrapped(
            [['id' => 'pay2', 'state' => 'CHECKOUT', 'redirectUrl' => 'https://checkout.example/pay2']])];
        $this->assertSame('https://checkout.example/pay2',
                          $this->checkout->startCheckout(2, '10.01', 'GBP'));   // reconciled
        $this->assertSame('READY', DB::table('psp_attempt')->where('id', $stuck->id)->value('state'));
        $this->assertSame($posts, $this->posts());                 // ONE referenceId, no 2nd POST
    }

    public function test_a_failed_attempt_lets_a_new_checkout_start(): void
    {
        $this->psp = ['POST /api/v1/payments' =>                   // confirmed refusal, nothing created
            Http::response(['status' => 400, 'errorCode' => '2.01'], 400)];
        try {
            $this->checkout->startCheckout(3, '1.00', 'GBP');
            $this->fail('expected PspApiError');
        } catch (PspApiError $e) {
            $this->assertSame(400, $e->status);
        }
        $this->assertSame(0, DB::table('psp_attempt')                    // FAILED sits outside
            ->where('order_id', 3)->whereIn('state', ['IN_FLIGHT', 'READY'])->count());  // the index
        $this->psp = ['POST /api/v1/payments' => self::wrapped(
            ['id' => 'pay3', 'state' => 'CHECKOUT', 'redirectUrl' => 'https://checkout.example/pay3'])];
        $this->assertSame('https://checkout.example/pay3',         // a NEW attempt, a NEW ref
                          $this->checkout->startCheckout(3, '1.00', 'GBP'));
        $this->assertSame(2, DB::table('psp_attempt')->where('order_id', 3)->count());
    }

    public function test_checkout_inside_an_open_transaction_is_refused(): void
    {
        $this->expectException(LogicException::class);             // it could not commit the claim
        DB::transaction(fn () => $this->checkout->startCheckout(2, '10.01', 'GBP'));
    }

    public function test_refund_timeout_then_retry_does_not_refund_twice(): void
    {
        $this->psp = ['POST /api/v1/payments' => self::timeout(), self::FIND => self::wrapped([])];
        try {
            $this->checkout->refundOrder(1, 'rk-1', '5.00', 'GBP');
            $this->fail('expected RefundOutcomeUnknown');
        } catch (RefundOutcomeUnknown) {
        }
        $stuck = DB::table('psp_refund_attempt')->where('refund_key', 'rk-1')->first();
        $this->assertSame('IN_FLIGHT', $stuck->state);                       // the row SURVIVED
        $this->assertSame(0, bccomp($this->fetchOrder(1)->refunded_amount, '5', 5));  // and so did
        // The PSP had processed it; only the client timed out. The retry must  // the reservation
        // reconcile the SAME referenceId, never POST a second REFUND.
        $posts = $this->posts();
        $this->psp = ["GET /api/v1/payments?referenceId.eq={$stuck->reference_id}" =>
            self::wrapped([['id' => 'rf1'] + self::REFUND_5])];
        $this->assertSame('rf1', $this->checkout->refundOrder(1, 'rk-1', '5.00', 'GBP')->id);
        $this->assertSame($posts, $this->posts());                           // no second payout
        $this->assertSame(0, bccomp($this->fetchOrder(1)->refunded_amount, '5', 5));  // once
    }

    public function test_concurrent_refunds_with_the_same_key_post_once(): void
    {
        $this->psp = [
            'POST /api/v1/payments' => function () {
                usleep(300_000);
                return self::wrapped(['id' => 'rf9'] + self::REFUND_5);
            },
            self::FIND => self::wrapped([]),          // the non-owner reconciles, finds nothing yet
            'GET /api/v1/payments/rf9' => self::wrapped(['id' => 'rf9'] + self::REFUND_5),
        ];
        $outcomes = $this->race(fn () => $this->checkout->refundOrder(1, 'rk-9', '5.00', 'GBP'));
        $this->assertSame(1, array_sum(array_column($outcomes, 1)));  // ONE payout, never two
        foreach ($outcomes as [$outcome]) {
            $this->assertContains($outcome, ['rf9', RefundOutcomeUnknown::class]);
        }
        $this->assertSame(0, bccomp($this->fetchOrder(1)->refunded_amount, '5', 5));  // reserved once
    }

    public function test_crash_after_an_accepted_refund_post_does_not_post_again(): void
    {
        // The crash state: attempt committed IN_FLIGHT, amount reserved, the PSP holds the payment.
        [$attempt] = $this->checkout->reserveRefund(1, 'rk-2', '5.00', 'GBP');
        $this->psp = ["GET /api/v1/payments?referenceId.eq={$attempt->reference_id}" =>
            self::wrapped([['id' => 'rf2'] + self::REFUND_5])];
        $this->assertSame('rf2', $this->checkout->refundOrder(1, 'rk-2', '5.00', 'GBP')->id);
        $this->assertSame(0, $this->posts());                         // reconciled, never re-POSTed
        $this->assertSame('DONE', DB::table('psp_refund_attempt')->where('id', $attempt->id)->value('state'));
    }

    public function test_a_refused_refund_releases_its_reservation_and_stays_refused(): void
    {
        $this->psp = ['POST /api/v1/payments' => Http::response(['status' => 400, 'errorCode' => '2.01'], 400)];
        try {
            $this->checkout->refundOrder(1, 'rk-3', '5.00', 'GBP');
            $this->fail('expected PspApiError');
        } catch (PspApiError) {
        }
        $this->assertSame(0, bccomp($this->fetchOrder(1)->refunded_amount, '0', 5));  // given back
        $this->expectException(RefundFailed::class);  // same key: the same answer, no 409, no POST
        try {
            $this->checkout->refundOrder(1, 'rk-3', '5.00', 'GBP');
        } finally {
            $this->assertSame(1, $this->posts());
        }
    }
}
```

The replay-job test (a stale `AUTHORIZED` receipt closed as `superseded`) exercises the hardened
variant: `references/hardening-concurrency.md` §2.
