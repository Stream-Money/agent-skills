# Stream — Regional payment methods

Request bodies and rules for the regional methods Stream merchants use most:
India UPI/IMPS, South Africa EFT, Kenya and West & Central Africa mobile money,
and Colombia bank payments and Bre-B. They replace the per-region PDF guides.

Every body below is the **same** as the matching example in the public API
reference's example picker, under the same name, and each one is checked
against `openapi/gateway-api.json` when the docs are built. The notes under each
body are the rules that make that method work: follow them as written.

Payment methods are enabled **per shop**. A method being listed here does not
mean it is live for the merchant you are working with. Confirm with the merchant,
or with one sandbox call, before building around it.

---

## 1. Choosing the flow

| Flow | How it works | Used by |
|---|---|---|
| **Redirect** (default) | `POST /api/v1/payments` → send the customer to `result.redirectUrl` → final state by webhook / `GET`. | Everything below unless noted; **only** option for South Africa EFT and Colombia bank payment. |
| **Server-to-server (S2S)** | `POST` → keep `result.id`, **do not redirect** → `PATCH /api/v1/payments/{id}` with `{"customerIp": "<customer's real IP>"}` → show what `result.externalRefs` returns (e.g. `qrCode`) → final state by webhook / `GET`. | Kenya mobile money, Colombia Bre-B QR, India UPI. **Must be enabled for the shop by Stream** (`deposit-and-withdrawal.md`). |
| **P2P redirect (India)** | One `POST` that already carries `additionalParameters.customerIp` (and `upiVpa`) → redirect. **No `PATCH`.** | India UPI P2P. |

S2S rules that are easy to get wrong:

- `customerIp` is the **customer's** public IP from the original browser
  request (honour your proxy's `X-Forwarded-For` correctly). It is never your
  server's IP.
- The `PATCH` is synchronous and returns a normal payment response. A `PENDING`
  state there is expected and is **not** a failure.
- If neither the redirect is opened nor the `PATCH` is made, the payment is
  closed automatically, by default **20 minutes** after creation. Map that to
  "expired / retry", not to an error.

### Field conventions across these methods

- `amount` is a JSON **number** (`500`, `200000.10`), never a string.
- `customer.phone` = country dialling code without `+`/`00`, a space, then the
  national number without the leading `0`: `91 9895270649`, `254 712345678`.
- `billingAddress.countryCode` must match the method's country.
- `additionalParameters` values are **strings** (the schema says so), including
  numeric-looking codes: `"bankCode": "37"`.
- `returnUrl` is camelCase. `return_url` is not an API field — do not send it.

---

## 2. India — UPI and IMPS

### (India) UPI deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10006",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "UPI",
  "amount": 500,
  "currency": "INR",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "91 9895270649"
  },
  "billingAddress": {
    "countryCode": "IN"
  }
}
```

- **Phone format:** `91`, a space, then a 10-digit number starting with `6`, `7`, `8` or `9`. That is 13 characters in total, e.g. `91 9xxxxxxxxx`. Do not add `+` or `00`.
- `billingAddress.countryCode` must be `IN`.
- **Server-to-server:** instead of redirecting, call Execute deposit with `{"customerIp": "<customer's real IP>"}`. The response carries the payment QR in `result.externalRefs.qrCode`.
- **Timeout:** if the customer never opens `redirectUrl` and you never call `PATCH`, the payment is closed automatically, by default **20 minutes** after it was created.
- If your setup requires the payer's VPA, use **(India) UPI deposit with payer VPA**.

### (India) UPI deposit with payer VPA

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10007",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "UPI",
  "amount": 500,
  "currency": "INR",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "91 9895270649"
  },
  "billingAddress": {
    "countryCode": "IN"
  },
  "additionalParameters": {
    "upiVpa": "customer@okbank"
  }
}
```

- **Only send `upiVpa` if Stream told the merchant their setup needs it.** Some providers in seamless mode require the VPA (UPI ID) the customer will pay **from**. Ask the customer for it before you create the payment.
- Phone format: `91 9xxxxxxxxx` (13 characters, starting `6`–`9` after `91 `).
- `billingAddress.countryCode` must be `IN`.

### (India) UPI P2P deposit (redirect)

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10008",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "UPI",
  "amount": 500,
  "currency": "INR",
  "customer": {
    "referenceId": "customer_42",
    "email": "john.doe@example.com"
  },
  "billingAddress": {
    "countryCode": "IN"
  },
  "additionalParameters": {
    "upiVpa": "customer@okbank",
    "customerIp": "203.0.113.10"
  }
}
```

**P2P redirect is a single request.** Send the customer's real IP in `additionalParameters.customerIp` here, not in a separate `PATCH`. Then redirect the customer to the `redirectUrl` from the response.

- Status checks and webhooks work the same as for any other payment.
- `billingAddress.countryCode` must be `IN`.

### (India) UPI withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10006",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "UPI",
  "amount": 10,
  "currency": "INR",
  "customer": {
    "referenceId": "customer_42",
    "email": "john.doe@example.com"
  },
  "billingAddress": {
    "countryCode": "IN"
  },
  "additionalParameters": {
    "upiVpa": "customer@okbank"
  }
}
```

- **Available only if UPI payouts are enabled for the shop.** Ask Stream support if unsure.
- `additionalParameters.upiVpa` is the VPA (UPI ID) the funds are paid **to**.
- `billingAddress.countryCode` must be `IN`.

### (India) IMPS bank withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10007",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "IMPS",
  "amount": 200000.10,
  "currency": "INR",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "91 9895270649",
    "accountNumber": "072180011751301416"
  },
  "billingAddress": {
    "countryCode": "IN"
  },
  "additionalParameters": {
    "ifscCode": "ABHY0065001"
  }
}
```

- **Available only if bank payouts are enabled for the shop.** `paymentMethod` can also be `RTGS`. Ask Stream support which one is enabled.
- `customer.accountNumber` is the recipient's bank account. `additionalParameters.ifscCode` is the 11-character IFSC of their branch.
- Phone format: `91 9xxxxxxxxx` (13 characters, starting `6`–`9` after `91 `).
- `billingAddress.countryCode` must be `IN`.

### (India) Sandbox – simulate a result

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10009",
  "description": "Sandbox test",
  "paymentType": "DEPOSIT",
  "paymentMethod": "UPI",
  "amount": 2000,
  "currency": "INR",
  "returnUrl": "https://merchant.example.com/return",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "91 9678954785"
  },
  "billingAddress": {
    "countryCode": "IN"
  },
  "additionalParameters": {
    "desiredResult": "COMPLETED"
  }
}
```

**Sandbox only** (`https://app-demo.stream.money`). The amount decides the final state:

- `2000` INR → `COMPLETED`
- `4000` INR → `DECLINED`
- `3000` INR → `CANCELLED`
- any other amount → `CANCELLED`

The payment moves to that state automatically after **about 2 minutes**, and you receive the webhook as you would in production.

- Leave out `returnUrl` if you integrate server-to-server.

---

## 3. Africa — South Africa EFT, Kenya and West & Central Africa mobile money

### (South Africa) EFT bank deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10001",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "NETBANKING",
  "amount": 1085,
  "currency": "ZAR",
  "returnUrl": "https://merchant.example.com/return",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "27 712345678",
    "accountNumber": "1234567890123"
  },
  "billingAddress": {
    "countryCode": "ZA"
  },
  "additionalParameters": {
    "bankTransferMethod": "EFT",
    "bankCode": "454"
  }
}
```

**Redirect only.** Send the customer to the `redirectUrl` returned in the response. There is no server-to-server flow for this method.

- `additionalParameters.bankTransferMethod` must be `EFT`.
- `additionalParameters.bankCode` is the customer's bank. See South African bank codes (`reference-data.md`).
- `customer.accountNumber` is the customer's bank account number.
- `billingAddress.countryCode` must be `ZA` and `currency` must be `ZAR`.
- Send `amount` as a JSON number (`1085`), not a string (`"1085"`).

### (South Africa) EFT bank withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10001",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "NETBANKING",
  "amount": 1085,
  "currency": "ZAR",
  "returnUrl": "https://merchant.example.com/return",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "27 712345678",
    "accountNumber": "1234567890123"
  },
  "billingAddress": {
    "countryCode": "ZA"
  },
  "additionalParameters": {
    "bankTransferMethod": "EFT",
    "bankCode": "454",
    "customerIp": "203.0.113.10"
  }
}
```

- **Include `additionalParameters.customerIp`**: the customer's real public IP address, not your server's.
- `customer.accountNumber` is the bank account the funds are paid **to**. Check it carefully, because a payout to a wrong account cannot be pulled back.
- `additionalParameters.bankCode` is the recipient's bank. See South African bank codes (`reference-data.md`).
- `billingAddress.countryCode` must be `ZA` and `currency` must be `ZAR`.

### (Kenya) Mobile money deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10002",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "MOBILE_MONEY",
  "amount": 200,
  "currency": "KES",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "phone": "254 712345678"
  },
  "billingAddress": {
    "countryCode": "KE"
  }
}
```

There are two ways to complete this payment:

- **Redirect:** send the customer to the `redirectUrl` from the response.
- **Server-to-server:** ignore `redirectUrl` and keep the payment `id`. Then call Execute deposit (`PATCH /api/v1/payments/{id}`) with `{"customerIp": "<customer's real IP>"}`.

Either way, the final result arrives by webhook (`webhooks.md`), or by polling `GET /api/v1/payments/{id}`.

- `customer.phone` is the mobile money wallet number: `254`, a space, then the number without the leading `0`.
- `billingAddress.countryCode` must be `KE` and `currency` must be `KES`.

### (Kenya) Mobile money withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10002",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "MOBILE_MONEY",
  "amount": 100,
  "currency": "KES",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "phone": "254 712345678"
  },
  "billingAddress": {
    "countryCode": "KE"
  }
}
```

- `additionalParameters.mobileMoneyOperator` is **not** required in Kenya.
- `customer.phone` is the wallet the funds are paid **to**: `254`, a space, then the number without the leading `0`.
- `billingAddress.countryCode` must be `KE` and `currency` must be `KES`.

### (West & Central Africa) Mobile money deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10003",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "MOBILE_MONEY",
  "amount": 200,
  "currency": "XOF",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "phone": "225 0778315017"
  },
  "billingAddress": {
    "countryCode": "CI"
  },
  "additionalParameters": {
    "mobileMoneyOperator": "orange",
    "otp": "1234"
  }
}
```

This example is for Côte d'Ivoire. For another country, set its `currency`, `billingAddress.countryCode` and phone prefix.

- `additionalParameters.mobileMoneyOperator` is **optional**. If you leave it out, the customer picks the operator on Stream's checkout page.
- `additionalParameters.otp` applies **only to `orange`**. If the operator is `orange` and you leave `otp` out, the checkout page asks the customer for it.
- Supported `mobileMoneyOperator` values by country:
  `CI`: mtn, moov, orange, wave ·
  `SN`: orange, expresso, free, wave ·
  `GN`: orange ·
  `ML`: orange, moov ·
  `BF`: orange, moov ·
  `BJ`: moov, mtn ·
  `TG`: tmoney, moov ·
  `CM`: orange, mtn

### (West & Central Africa) Mobile money withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10003",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "MOBILE_MONEY",
  "amount": 100,
  "currency": "XOF",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "phone": "225 0778315017"
  },
  "billingAddress": {
    "countryCode": "CI"
  },
  "additionalParameters": {
    "mobileMoneyOperator": "orange"
  }
}
```

- **`additionalParameters.mobileMoneyOperator` is required for withdrawals.** (It is optional for deposits.)
- Use the operator values listed under Regional reference values (`reference-data.md`) for the customer's country.
- `customer.phone` is the wallet the funds are paid **to**, including the country dialling code.
- This example is for Côte d'Ivoire. For another country, set its `currency`, `billingAddress.countryCode` and phone prefix.

---

## 4. Colombia — bank payments and Bre-B

### (Colombia) Bank payment deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10004",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "NETBANKING",
  "amount": 2000,
  "currency": "COP",
  "returnUrl": "https://merchant.example.com/return",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "57 3001234567"
  },
  "billingAddress": {
    "countryCode": "CO"
  }
}
```

**Redirect only.** Send the customer to the `redirectUrl` returned in the response. To pay by Bre-B QR without a redirect, use the **(Colombia) Bre-B QR deposit** example instead.

- The field is `returnUrl` (camelCase). `return_url` is not an API field.
- `billingAddress.countryCode` must be `CO` and `currency` must be `COP`.

### (Colombia) Bre-B QR deposit

`POST /api/v1/payments`

```json
{
  "referenceId": "order_10005",
  "description": "Account top-up",
  "paymentType": "DEPOSIT",
  "paymentMethod": "NETBANKING",
  "amount": 2000,
  "currency": "COP",
  "returnUrl": "https://merchant.example.com/return",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "57 3001234567"
  },
  "billingAddress": {
    "countryCode": "CO"
  },
  "additionalParameters": {
    "documentType": "CO_CC",
    "documentNumber": "1234567890",
    "account_type": "BREB",
    "bankCode": "37"
  }
}
```

**Server-to-server flow, in two calls:**

1. Create the payment with this request and keep the `id` from the response.
2. Call Execute deposit (`PATCH /api/v1/payments/{id}`) with `{"customerIp": "<customer's real IP>"}`. The response carries the QR payload in **`result.externalRefs.qrCode`**. Render it as a QR code for the customer to scan.

- `account_type` is **snake_case**, unlike the other fields, and must be `BREB`.
- `bankCode` must be `37` (Bre-B).
- `documentType` / `documentNumber` are the customer's ID document. See Colombian document types (`reference-data.md`).

### (Colombia) Bank account withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10004",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "NETBANKING",
  "amount": 1000,
  "currency": "COP",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "57 3001234567",
    "accountName": "John Doe"
  },
  "billingAddress": {
    "countryCode": "CO"
  },
  "additionalParameters": {
    "documentType": "CO_CC",
    "documentNumber": "1234567890",
    "account_type": "SAVINGS",
    "bankCode": "1",
    "accountNumber": "1212121212123"
  }
}
```

- `account_type` (**snake_case**) is `SAVINGS` or `CHECKING`. For a Bre-B key, use the **(Colombia) Bre-B key withdrawal** example.
- `bankCode` is a **numeric code from the Colombian bank list (`reference-data.md`)**. Any code not on the list is rejected with HTTP `422`.
- `accountNumber` sits in `additionalParameters` for this method, not in `customer`.
- `customer.accountName` is the account holder's name as the bank knows it.
- `documentType` / `documentNumber` are the account holder's ID document.

### (Colombia) Bre-B key withdrawal

`POST /api/v1/payments`

```json
{
  "referenceId": "payout_10005",
  "description": "Withdrawal",
  "paymentType": "WITHDRAWAL",
  "paymentMethod": "NETBANKING",
  "amount": 1000,
  "currency": "COP",
  "customer": {
    "referenceId": "customer_42",
    "firstName": "John",
    "lastName": "Doe",
    "email": "john.doe@example.com",
    "phone": "57 3001234567",
    "accountName": "John Doe"
  },
  "billingAddress": {
    "countryCode": "CO"
  },
  "additionalParameters": {
    "documentType": "CO_CC",
    "documentNumber": "1234567890",
    "account_type": "BREB",
    "bankCode": "37",
    "accountNumber": "@B0104GCFF7540D39"
  }
}
```

- To pay out to a Bre-B key, set `account_type` to `BREB` and `bankCode` to `37`. Then put the **Bre-B key itself in `accountNumber`**, starting with `@`.
- `documentType` / `documentNumber` are the recipient's ID document. See Colombian document types (`reference-data.md`).
- `customer.accountName` is the recipient's name.

---

## 5. Server-to-server step 2, and what comes back

### (Server-to-server) Submit the customer's IP

`PATCH /api/v1/payments/{id}`

```json
{
  "customerIp": "203.0.113.10"
}
```

This is **step 2 of a server-to-server deposit**. Use it with methods that support it, e.g. Kenya mobile money, Colombia Bre-B QR and India UPI.

1. Create the payment with `POST /api/v1/payments` and keep `result.id`. Do **not** redirect the customer.
2. Call this endpoint with that `id` in the path and the customer's **real public IP** in the body. Do not send your server's IP.
3. The response is a normal payment response. Depending on the method, `result.externalRefs` contains what to show the customer, e.g. `qrCode`.
4. Wait for the webhook (`webhooks.md`) or poll `GET /api/v1/payments/{id}` for the final state.

**Timeout:** if this call is never made, the payment is closed automatically, by default **20 minutes** after creation.

Ask Stream support before using this endpoint: server-to-server must be enabled for the shop.

### (Colombia) Bre-B QR deposit – QR code returned

`PATCH /api/v1/payments/{id} — response 200`

```json
{
  "timestamp": "2026-08-26T09:45:12.664+00:00",
  "status": 200,
  "result": {
    "id": "2a053fd001b3462e93cb993e4903a19c",
    "referenceId": "order_10005",
    "created": "2026-08-26T09:44:58",
    "paymentType": "DEPOSIT",
    "state": "PENDING",
    "description": "Account top-up",
    "paymentMethod": "NETBANKING",
    "amount": 2000.00,
    "currency": "COP",
    "customer": {
      "referenceId": "customer_42",
      "firstName": "John",
      "lastName": "Doe",
      "email": "john.doe@example.com",
      "phone": "57 3001234567"
    },
    "billingAddress": {
      "countryCode": "CO"
    },
    "externalRefs": {
      "qrCode": "mock-breb-qr-e547e98c-56bf-46c2-b6e5-54aef0f6db79-c4170f4c",
      "transactionId": "2a053fd001b3462e93cb993e4903a19c"
    },
    "additionalParameters": {
      "documentType": "CO_CC",
      "documentNumber": "1234567890",
      "bankCode": "37"
    }
  }
}
```

- **`result.externalRefs.qrCode`** is the Bre-B payload. Encode it as a QR image and show it to the customer.
- The state is still `PENDING`. The final state arrives by webhook (`webhooks.md`).
- The sandbox returns a mock value (`mock-breb-qr-…`), not a scannable code.
