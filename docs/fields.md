## Standard output formats

Reusable format types referenced by every field below.

| Format ID            | Shape                                                | Allowed values                                                  |
| -------------------- | ---------------------------------------------------- | --------------------------------------------------------------- |
| `yes_no`             | One word                                             | `Yes` or `No`                                                   |
| `yes_no_na`          | One word                                             | `Yes`, `No`, or `N/A`                                           |
| `yes_no_with_detail` | Single line: `No`, or `Yes, [concise action summary]` | `No`, or `Yes, …`                                               |
| `date`               | `DD/MM/YYYY`                                         | Any valid date, or blank if not determinable                    |
| `frequency`          | Short frequency                                      | `annually`, `X years`, or blank if not determinable             |
| `enum`               | Single value from a fixed list                       | Listed per field                                                |

### Parties

**Sections used**

- Whole sections: `COVER` (cover page), `LEASE_PARTICULARS` (lease summary / particulars), `PRESCRIBED_CLAUSES` (Land Registry prescribed clauses)
- Semantic topics: `guarantor_surety` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Landlord`, `Tenant`, `Lessor`, `Lessee`, `Guarantor`, `Surety`)

| Field            | Output format                                   | Allowed / expected outputs                                                                                                                   | Simple example             |
| ---------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Original Lessor  | Full legal name only, or blank                  | Extract only the original landlord’s legal name. Leave blank if input is empty.                                                              | `LANDLORD LIMITED`         |
| Current Lessor   | Full legal name only, or blank                  | Extract only the current landlord’s legal name. Leave blank if input is empty or says the name is unclear/not found.                         | `CURRENT LANDLORD LIMITED` |
| Original Lessee  | Full legal name only, or blank                  | Extract only the original tenant’s legal name. Leave blank if input is empty or says the name is unclear/not found.                          | `TENANT TRADING LTD`       |
| Current Lessee   | Full legal name only, or blank                  | Extract only the current tenant’s legal name. Leave blank if input is empty or says the name is unclear/not found.                           | `CURRENT TENANT LTD`       |
| Guarantor Exists | `yes_no`                                        | State whether the text explicitly mentions a Guarantor. Do not treat Surety as Guarantor.                                                    | `Yes`                      |
| Guarantor Name   | Guarantor name only, `Name not found`, or blank | Extract only the guarantor’s name. Use `Name not found` if a guarantor is mentioned but not named. Leave blank if no guarantor is mentioned. | `GLOBALWIDE PLC`           |
| Surety Exists    | `yes_no`                                        | State whether the text explicitly mentions a Surety. Do not treat Guarantor as Surety.                                                       | `Yes`                      |
| Surety Name      | Surety name only, `Name not found`, or blank    | Extract only the surety’s name. Use `Name not found` if a surety is mentioned but not named. Leave blank if no surety is mentioned.          | `PARENT COMPANY LTD`       |

### Property

**Sections used**

- Whole sections: `COVER` (cover page), `LEASE_PARTICULARS` (lease summary / particulars), `PRESCRIBED_CLAUSES` (Land Registry prescribed clauses)
- Semantic topics: `premises` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Premises`, `Property`, `Demised Premises`)

| Field            | Output format                           | Allowed / expected outputs                                                                                            | Simple example                                                                                        |
| ---------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Property Address | Clean standalone address only, or blank | Extract only the property address, including postcode if present.                                                      | `15 Queen Street, Bristol BS1 4AB`                                                                    |
| Demised Premises | Two-line summary                        | Summarise the postal address and the demised premises description. Leave either line blank after the colon if missing. | `Postal Address: 14–16 King Street, Manchester M2 6AQ`<br>`Description: office suite on the second floor` |

### Permitted use

**Sections used**

- Whole sections: `LEASE_PARTICULARS` (lease summary / particulars)
- Semantic topics: `permitted_use` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Permitted Use`, `User`)

| Field         | Output format                      | Allowed / expected outputs                                                                         | Simple example                     |
| ------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Permitted Use | Concise description only, or blank | State only the current permitted use of the property. Leave blank if no permitted use is described. | `Offices within Use Class E(g)(i)` |

### Lease details

**Sections used**

- Whole sections: `COVER` (cover page), `LEASE_PARTICULARS` (lease summary / particulars)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Term`, `Commencement Date`, `Term Commencement Date`)

| Field       | Output format        | Allowed / expected outputs                                                                                                                                         | Simple example                                            |
| ----------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Lease Date  | `date`               | Extract the lease date. Leave blank if no clear lease date is identified.                                                                                           | `25/03/2025`                                              |
| Term        | Concise text summary | Summarise the lease term, including length, commencement date, and expiry date where stated. Include whether it is not open ended if the term or expiry is defined. | `10 years from 25/03/2025 to 24/03/2035. Not open ended.` |
| Expiry Date | `date`               | Extract or calculate the lease expiry date as the last day of the term.                                                                                             | `24/03/2035`                                              |

### Financials

**Sections used**

- Whole sections: `LEASE_PARTICULARS` (lease summary / particulars)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Rent`, `Basic Rent`, `Yearly Rent`)

| Field         | Output format                             | Allowed / expected outputs                                      | Simple example      |
| ------------- | ----------------------------------------- | --------------------------------------------------------------- | ------------------- |
| Original Rent | Monetary amount and period only, or blank | Extract the original rent amount, currency, and payment period. | `£50,000 per annum` |

### Statutory status

**Sections used**

- Whole sections: `LEASE_PARTICULARS`, `PRESCRIBED_CLAUSES`
- Semantic topics: `landlord_tenant_act_1954` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Security of Tenure`, `Contracting Out`, `1954 Act`)

| Field        | Output format         | Allowed / expected outputs                                                                                         | Simple example |
| ------------ | --------------------- | ------------------------------------------------------------------------------------------------------------------ | -------------- |
| L&T Act 1954 | `Inside` or `Outside` | Main lease inside or outside Part II of the 1954 Act. Inferential Inside (medium) may set `needs_review=true`. | `Inside`       |

### Rent review

**Sections used**

- Whole sections: `LEASE_PARTICULARS` (front matter — lease summary)
- Semantic topics: `rent_review` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json` (e.g. `Rent`, `Review Date`, `Open Market Rent`, `Term`)

| Field                            | Output format        | Allowed / expected outputs                                                                                                                                     | Simple example                                                                                                                        |
| -------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Date, Rent Review Dates          | `date`               | Any determinable first rent review date, or blank if not determinable                                                                                          | `25/12/2029`                                                                                                                          |
| Frequency, Rent Review Frequency | `frequency`          | `annually`, `X years`, or blank if not determinable                                                                                                            | `5 years`                                                                                                                             |
| Time of Essence                  | `yes_no`             | `Yes` or `No`                                                                                                                                                  | `Yes`                                                                                                                                 |
| Notice Provisions                | `yes_no_with_detail` | `No`, or `Yes, [concise action summary]`                                                                                                                       | `Yes, written notice must be sent by recorded delivery to the Tenant's registered office at least six months before the review date.` |
| Basis of Valuation               | `enum`               | `Open Market Value`, `Indexation (RPI)`, `Indexation (CPI)`, `Stepped rent`, `Fixed increase`, `Turnover rent`, `No rent review`, or blank if not identifiable | `Open Market Value`                                                                                                                   |
| Upwards Only                     | `yes_no`             | `Yes` or `No` — answering "Can the rent be reduced on review?"                                                                                                 | `No`                                                                                                                                  |


### Open Market Value — Disregards

**Sections used**

- Semantic topics: `rent_review` (tagged numbered clauses + schedules)

| Field                           | Output format | Allowed / expected outputs | Simple example |
| ------------------------------- | ------------- | -------------------------- | -------------- |
| Occupation, Open Market Value   | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `Yes`          |
| Goodwill, Open Market Value     | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `Yes`          |
| Improvements, Open Market Value | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `Yes`          |
| Licenses                        | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `No`           |
| Reinstatement                   | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `No`           |
| Rent Free, Open Market Value    | `yes_no_na`   | `Yes`, `No`, or `N/A`      | `Yes`          |

### Open Market Value — Assumptions

**Sections used**

- Semantic topics: `rent_review` (tagged numbered clauses + schedules)

| Field                                | Output format     | Allowed / expected outputs                                                                                                                            | Simple example |
| ------------------------------------ | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| As Whole                             | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| In Part                              | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| Without Fine/Premium                 | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| Vacant Possession, Open Market Value | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| Fit For Immediate Occupation & Use   | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| Term, Open Market Value              | Concise term only | Specific term such as `10 years`, `15 years`, `The residue of the term`, `A term expiring on 24th December 2045`, `N/A`, or blank if not identifiable | `15 years`     |
| Lease Terms                          | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |
| Willing Parties, Open Market Value   | `yes_no_na`       | `Yes`, `No`, or `N/A`                                                                                                                                 | `Yes`          |

### Repairs and decoration

**Sections used**

- Semantic topics: `repairs_decoration` (tagged numbered clauses + schedules)
- Semantic topics: `service_charge` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json`

| Field                           | Output format                      | Allowed / expected outputs                                                                    | Simple example          |
| ------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------- |
| Internal, Internal Repair       | One value only                     | `Landlord`, `Tenant`, `Mixed`, or blank if not clearly stated                                 | `Tenant`                |
| External, External Repair       | One value only                     | `Landlord`, `Tenant`, `Mixed`, or blank if not clearly stated                                 | `Landlord`              |
| Internal, Internal Decoration   | `[Responsible Party], [Frequency]` | `Landlord, [frequency]`, `Tenant, [frequency]`, `Mixed, [summary]`, or blank if not mentioned | `Tenant, Every 5 years` |
| External, External Decoration   | `[Responsible Party], [Frequency]` | `Landlord, [frequency]`, `Tenant, [frequency]`, `Mixed, [summary]`, or blank if not mentioned | `Landlord, Every 5 years` |

### Alterations

**Sections used**

- Semantic topics: `alterations` (tagged numbered clauses + schedules)

| Field                             | Output format                            | Allowed / expected outputs                                                                                                                                 | Simple example                                                                                                                                 |
| --------------------------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| External Alterations              | One or two short plain-English sentences | Summarise whether external alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.                 | `External alterations are prohibited without the Landlord's prior written consent.`                                                              |
| Internal Alterations              | One or two short plain-English sentences | Summarise whether internal alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.                     | `Internal alterations are permitted with the Landlord's prior written consent.`                                                                  |
| Structural Alterations            | One or two short plain-English sentences | Summarise whether structural alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.                 | `Structural alterations are prohibited without the Landlord's prior written consent.`                                                          |
| Non-Structural Alterations        | One or two short plain-English sentences | Summarise whether non-structural alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.             | `Non-structural alterations are permitted with the Landlord's prior written consent.`                                                          |
| Demountable Partition Alterations | One or two short plain-English sentences | Summarise whether demountable partitions are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.                 | `Demountable partitions may be installed with the Landlord's prior written consent.`                                                           |
| Electrics Alterations             | One or two short plain-English sentences | Summarise whether electrical alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.                     | `Electrical alterations require the Landlord's prior written consent and must be carried out by qualified contractors.`                          |
| Conduits/Services Alterations     | One or two short plain-English sentences | Summarise whether conduits/services alterations are prohibited, permitted, or conditional. Include any Landlord consent requirement where stated.            | `Alterations to conduits or services are prohibited without the Landlord's prior written consent.`                                             |

### Alienation

**Sections used**

- Semantic topics: `alienation` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json`

| Field         | Output format                            | Allowed / expected outputs                                                                                                                                                                                                                                 | Simple example                                                                                                                |
| ------------- | ---------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Assign Whole  | One or two short plain-English sentences | State whether assignment of the whole is prohibited or permitted. Include whether Landlord consent is required, whether it must be prior or in writing, whether consent cannot be unreasonably withheld or delayed, and any stated exceptions or conditions. | `Assignment of the whole is permitted with the Landlord's prior written consent, not to be unreasonably withheld or delayed.` |
| Sublet Whole  | One or two short plain-English sentences | State whether subletting the whole is prohibited or permitted. Include whether Landlord consent is required, the standard of consent, and any other stated restrictions or conditions.                                                                      | `Subletting the whole is permitted with the Landlord's consent, not to be unreasonably withheld or delayed.`                  |
| Sublet Parts  | One or two short plain-English sentences | State whether subletting part is prohibited, permitted, or not mentioned. Include whether Landlord consent is required, whether it must not be unreasonably withheld, and any stated restrictions or conditions.                                            | `Subletting part is prohibited.`                                                                                              |

### Rights granted

**Sections used**

- Semantic topics: `rights_granted` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json`

| Field             | Output format                                             | Allowed / expected outputs                                                                                                                                                                          | Simple example                       |
| ----------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| Vehicle Parking   | `No`, `Yes, [Number] spaces`, or `Yes, [concise summary]` | State whether car parking rights exist. If a specific number is stated, give the number of spaces. If rights exist but no number is stated, give a concise summary of the right.                     | `Yes, 2 spaces`                      |
| Bicycle Parking   | `No`, `Yes, [Number] spaces`, or `Yes, [concise summary]` | State whether bicycle parking rights exist. If a specific number is stated, give the number of spaces. If rights exist but no number is stated, give a concise summary of the right.                 | `Yes, Use of communal bicycle racks` |
| Motorbike Parking | `No`, `Yes, [Number] spaces`, or `Yes, [concise summary]` | State whether motorbike or motorcycle parking rights exist. If a specific number is stated, give the number of spaces. If rights exist but no number is stated, give a concise summary of the right. | `Yes, 1 space`                       |
| Fire Exit         | `yes_no`                                                  | State whether the text mentions a fire exit, emergency exit, or fire escape route on or serving the premises.                                                                                        | `Yes`                                |
| Loading/Unloading | `yes_no`                                                  | State whether the text mentions a loading bay, service yard, or right to load and unload goods or materials.                                                                                         | `Yes`                                |

### Break rights

**Sections used**

- Semantic topics: `break_option` (tagged numbered clauses + schedules)

| Field                     | Output format                        | Allowed / expected outputs                                                                                                                | Simple example                                                                                                                                                     |
| ------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tenant Break Conditions   | Concise structured summary, or blank | Summarise the conditions the Tenant must satisfy to exercise its break right. Leave blank if no Tenant break conditions are mentioned.     | `Tenant's Conditions: serve at least six months' prior written notice; pay all rent due up to the break date; give vacant possession on or before the break date.` |
| Landlord Break Conditions | Concise structured summary, or blank | Summarise the conditions the Landlord must satisfy to exercise its break right. Leave blank if no Landlord break conditions are mentioned. | `Landlord's Conditions: serve at least six months' prior written notice; state the intended break date in the notice.`                                             |

### Third party

**Sections used**

- Semantic topics: `third_party_determination` (tagged numbered clauses + schedules)

| Field                          | Output format                                                | Allowed / expected outputs                                                                                                                       | Simple example                                                                                               |
| ------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Third Party Determination      | `Arbitrator`, `Independent Expert`, `Mixed`, or `No mention` | Identify whether the third-party rent review resolver is expressly an arbitrator, independent expert, either/both, or not expressly specified.    | `Independent Expert`                                                                                         |
| Application by                 | `Landlord`, `Tenant`, `Landlord or Tenant`, or blank         | Identify which party can initiate the appointment of the third party. Leave blank if not clearly stated.                                          | `Landlord or Tenant`                                                                                         |
| Memorandum                     | `yes_no`                                                     | State whether the clause requires a memorandum recording the expert’s decision.                                                                   | `Yes`                                                                                                        |
| Interest on Backdated Increase | `No`, or `Yes, [single-sentence details]`                    | State whether interest is payable on a backdated rent increase. If yes, include the rate, calculation period, and calculation basis where stated. | `Yes, interest is payable at the Base Rate from the review date until payment, calculated on a daily basis.` |

### Insurance

**Sections used**

- Semantic topics: `insurance` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json`

| Field                                | Output format                  | Allowed / expected outputs                                                                                                                                         | Simple example |
| ------------------------------------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------- |
| Building Insurance                   | `Yes` or `No`                  | State whether the text mentions an obligation to insure the building, premises, or structure.                                                                       | `Yes`          |
| Building Insurance Responsibility    | `Landlord`, `Tenant`, or blank | Identify who is responsible for arranging or effecting the building insurance policy. Leave blank if unclear or not mentioned.                                      | `Landlord`     |
| Plate Glass Insurance                | `Yes` or `No`                  | State whether the text mentions an obligation to insure plate glass or all glass.                                                                                   | `Yes`          |
| Plate Glass Insurance Responsibility | `Landlord`, `Tenant`, or blank | Identify who is responsible for arranging or effecting the plate glass insurance policy. Leave blank if unclear or not mentioned.                                   | `Tenant`       |
| Engineering Insurance                | `Yes` or `No`                  | State whether the text mentions engineering insurance, including cover for plant, machinery, lifts, boilers, or similar equipment.                                  | `Yes`          |
| Engineering Insurance Responsibility | `Landlord`, `Tenant`, or blank | Identify who is responsible for arranging or effecting the engineering insurance policy. Leave blank if unclear or not mentioned.                                   | `Landlord`     |
| Third Party Liability                | `Yes` or `No`                  | State whether the Tenant is required to maintain public liability or third-party liability insurance.                                                               | `Yes`          |
| Loss of Rent                         | `Yes` or `No`                  | State whether the Landlord's insurance covers loss of rent or loss of rental income.                                                                                | `Yes`          |
| Loss of Rent Period                  | Duration only, or blank        | State the period of loss of rent cover. Leave blank if not stated or if loss of rent insurance is not mentioned.                                                    | `3 years`      |
| Cessor of Rent                       | `Yes` or `No`                  | State whether rent is suspended, abated, or ceases to be payable if the premises are damaged and unusable.                                                          | `Yes`          |
| Policy Effected By                   | `Landlord`, `Tenant`, or blank | Identify who takes out the main insurance policy. Leave blank if unclear, if both parties insure different things, or if no insurance obligation is mentioned.      | `Landlord`     |
| Policy Paid By                       | `Landlord`, `Tenant`, or blank | Identify who ultimately pays the main insurance premium. Use `Tenant` if the premium is recharged through insurance rent or service charge. Leave blank if unclear. | `Tenant`       |

### Service Charge

**Sections used**

- Semantic topics: `service_charge` (tagged numbered clauses + schedules)
- Definitions: relevant rows from `definitions_index.json`

| Field                        | Output format | Allowed / expected outputs                                                                                                                                                                             | Simple example |
| ---------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------- |
| Sinking Fund                 | `Yes` or `No` | State whether the lease provides for a sinking fund or reserve fund and explicitly requires the Tenant to contribute to it.                                                                             | `Yes`          |
| On Account                   | `Yes` or `No` | State whether the Tenant must pay service charge in advance, such as by interim charge, estimated service charge, or payments on account.                                                               | `Yes`          |
| Ad-Hoc Payments              | `Yes` or `No` | State whether the Landlord can demand additional one-off or irregular service charge payments outside the normal estimated budget.                                                                      | `Yes`          |
| Building                     | `Yes` or `No` | State whether service charge covers the main building, structure, exterior, roof, foundations, common areas, Building, or Estate.                                                                       | `Yes`          |
| Building Services            | `Yes` or `No` | State whether service charge covers building services such as lifts, HVAC, plant, machinery, boilers, water, electrical systems, or other mechanical/electrical services.                               | `Yes`          |
| Common Parts of the Building | `Yes` or `No` | State whether service charge covers internal building common parts, such as entrance halls, lobbies, staircases, lifts, or shared internal areas.                                                       | `Yes`          |
| Common Parts of the Estate   | `Yes` or `No` | State whether service charge covers estate common parts, such as access roads, car parks, landscaping, footpaths, or shared external estate facilities.                                                 | `Yes`          |
| Budget Process               | `Yes` or `No` | State whether the lease describes the Landlord preparing or providing a budget, estimate, or statement of anticipated service charge expenditure for the next period.                                   | `Yes`          |
| Reconciliation Process       | `Yes` or `No` | State whether the lease describes year-end reconciliation of estimated service charge against actual costs, including surplus, deficit, balancing charge, credit, or certificate of actual expenditure. | `Yes`          |