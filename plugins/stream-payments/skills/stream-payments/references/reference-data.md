# Stream — Reference data (document types, bank codes)

What lookup values a payment request may need, where they go in the request,
and where the authoritative lists live. Concrete per-method request bodies are
in `regional-methods.md`.

---

## 1. Where the lists live

This plugin covers the **Gateway API only**. Stream does not publish an API
endpoint for reference data, so the sources are, in order:

1. **The public docs**, "Regional reference values" page (URL in
   `wl-config.md`). It has South African and Colombian bank codes, mobile-money
   operators by country, and Colombian document and account types.
2. **The bundled spec** (`openapi/gateway-api.json`). Its enums are
   authoritative for **top-level schema fields**: `customer.documentType`,
   `customer.accountType`, `PaymentMethod`.
3. **Stream support**, for any method or country not on the docs page.

Do not invent codes. A code that is not on the list fails: Colombia rejects an
unknown `bankCode` with HTTP `422`, and the generic error `3.23 Bank does not
exist or not supported` means the same thing elsewhere.

## 2. Two places a value can go

The same concept appears in two places, and **the method decides which one**.
Do not "normalise" one into the other.

| Concept | Schema field (top level) | Provider-specific (`additionalParameters`, all strings) |
|---|---|---|
| ID document type | `customer.documentType`: enum from the spec, e.g. `BR_CPF`, `CO_CC` | `documentType`: Colombia NETBANKING / Bre-B |
| ID document number | `customer.documentNumber` (≤ 64) | `documentNumber`: Colombia |
| Account type | `customer.accountType`: `SAVINGS` \| `CHECKING` | `account_type` (**snake_case**): `SAVINGS` \| `CHECKING` \| `BREB`, Colombia |
| Bank | `customer.bankCode` / `customer.bank` | `bankCode`: South Africa EFT, Colombia |
| Account number | `customer.accountNumber` | `accountNumber`: Colombia withdrawals, Bre-B key |
| IFSC (India) | — | `ifscCode`: India IMPS withdrawal |

The rule: **copy the shape of the method's example in `regional-methods.md`
exactly.** For a method that has no example there, use the schema fields and
confirm with Stream support.

## 3. Document types

`customer.documentType` is an enum of per-country codes in the spec (e.g.
`AR_DNI`, `BR_CPF`, `CL_RUT`, `CO_CC`, `MX_CURP`, `PE_DNI`). Read the full
list from `components.schemas.DocumentType` in `openapi/gateway-api.json`;
do not copy it into code by hand. It is always sent together with
`customer.documentNumber`. There is no single `documentId` field.

Colombia's provider-specific `additionalParameters.documentType` takes:

| Value | Document |
|---|---|
| `CO_CC` | Cédula de Ciudadanía (national ID) |
| `CO_NIT` | NIT (tax ID, businesses) |
| `CO_CE` | Cédula de Extranjería (foreigner ID) |
| `CO_PP` | Pasaporte |

Errors `3.12`, `3.13`, `3.25` and `3.26` point at the document. Check the code,
the number format and the country.

## 4. Bank codes

Bank lists change, so treat the docs page as the source and refresh any cached
copy. If you must embed a list (e.g. a bank `<select>`), keep it in **one**
data file with a comment naming the docs page and the date it was copied.

- **South Africa EFT:** `additionalParameters.bankCode`, a 3-digit code, e.g.
  `454` = Capitec Business.
- **Colombia:** `additionalParameters.bankCode`, a numeric code, e.g. `1` =
  Bancolombia, `37` = Bre-B. Unknown codes → `422`.

## 5. Mobile-money operators

`additionalParameters.mobileMoneyOperator`, per country. The values are on the
docs page and in `regional-methods.md` §3. It is optional for deposits (the
checkout asks the customer) and required for withdrawals.
