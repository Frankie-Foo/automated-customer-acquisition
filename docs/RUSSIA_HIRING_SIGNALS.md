# Russia Hiring Signals

Russian account enrichment runs automatically during company-seed imports and precise public searches. Sales users do not enable a separate mode or complete another step.

## Trigger

The backend enables the strategy when the account contains one of these signals:

- Russia or a known Russian city in country/location fields.
- A `.ru` or `.рф` company domain.
- Russian-market context in the imported account reason.

## Processing

1. Search public hh.ru vacancy pages through the configured Brave, Tavily, or Google public-search provider.
2. Keep only results whose employer matches the imported company.
3. Reject archived vacancies and unrelated pages.
4. Identify retail, buyer, category, procurement, commercial, and development roles.
5. Save the job title, city, publication date, evidence URL, and expansion score in `contacts.source_context`.
6. Continue the existing domain resolution, decision-maker search, email verification, profile, and draft workflow.

Failure to obtain hiring signals never blocks the normal sourcing pipeline.

## Configuration

```yaml
sourcing:
  russia_hiring_signals:
    enabled: true
    official_api_enabled: false
    max_vacancies_per_company: 10
```

`official_api_enabled` remains `false` unless operations has confirmed official hh.ru API access. No additional key is required for the default public-search path.

## Safety Rules

- Candidate resumes and private contact data are not collected.
- Hiring evidence is treated as an account signal, not proof of a confirmed expansion plan.
- Messages remain drafts until reviewed under the existing outreach workflow.
