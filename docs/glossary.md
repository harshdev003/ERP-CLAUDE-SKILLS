# ERP glossary

One word per concept, used the same way in code, API, UI and docs
(see `rules/services/naming.md`). Add terms as the product grows.

| Term | Meaning | Not |
|---|---|---|
| Tenant / company | One customer organisation using the ERP; owns all its data | account, org (unless defined) |
| Customer | Someone the tenant sells to | client, buyer |
| Supplier | Someone the tenant buys from | vendor, seller |
| Item | A product or service the tenant buys, sells or stocks | product/SKU (unless both exist) |
| Invoice | Sales document billed to a customer | bill |
| Bill | Purchase document received from a supplier | invoice |
| Journal entry | Balanced set of ledger lines (debits = credits) | transaction |
| Posting | Making a document's ledger/stock effects final | saving |
| Warehouse | A location that holds stock | store, godown (UI may localise) |
| Stock move | One append-only change of quantity at a warehouse | adjustment (that is one kind of move) |
| Period | An accounting period that can be locked after close | month |
