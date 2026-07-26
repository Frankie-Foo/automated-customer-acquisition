# Iran enhanced sourcing

The system activates this mode automatically when a company seed contains an
Iranian country/location alias or an `.ir` company domain. Salespeople do not
need to choose a region or enable a switch.

## Public sources

The default priority is:

1. IranTalent for senior managers and experienced professionals.
2. JobVision for professional, retail, sales, marketing, product, and design roles.
3. Jobinja for digital, marketing, design, and technology roles.
4. Divar Jobs for local retail and service expansion.
5. Sheypoor Jobs for additional local coverage.
6. e-estekhdam for large-company and institutional hiring signals.

Search uses the existing Brave, Tavily, and Google CSE fallback chain. It reads
publicly indexed company and vacancy pages only; it does not access gated resume
databases or collect private candidate contact details.

## Automatic behavior

- Adds Persian and English decision-maker terms to public LinkedIn searches.
- Adds Persian website, distributor, representative, Telegram, and Instagram
  terms to company-domain discovery.
- Searches Iranian job platforms in Persian.
- Keeps the source URL, platform, matched role, publication date, and score.
- Uses hiring activity as explainable company-expansion evidence for account
  scoring, profile context, and personalized email drafts.
- Stops after the first credible platform by default to limit API usage.
- Treats search-provider errors as optional enrichment failures so imports keep
  running.

## Configuration

```yaml
sourcing:
  iran_hiring_signals:
    enabled: true
    max_queries_per_company: 3
    max_results_per_query: 5
    stop_after_first_match: true
```

Increasing `max_queries_per_company` improves coverage but consumes more public
search quota. The production default of three queries is the recommended
balance for batch imports.
