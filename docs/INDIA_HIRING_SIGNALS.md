# India hiring-signal specialization

The sourcing pipeline automatically enables this strategy when the lead location resolves to India. There is no user-facing switch.

## Public sources

The fallback order is:

1. Naukri public job pages
2. Indeed India public job pages
3. Foundit public job pages

Only pages already exposed through the configured public-search provider are read. The system does not log in to recruiter products, access gated resumes, or bypass platform controls.

## How the signal is used

For each company seed, the service:

1. Searches current public job listings.
2. Requires an exact-enough company-name match.
3. Looks for India-specific retail, commercial, procurement, channel, franchise, and store-operation roles.
4. Stores the source URL, matched role, publication date, and expansion score.
5. Adds the evidence to the customer profile used by person matching and personalized email generation.

An empty result or provider failure never blocks the normal lead-sourcing path.

## Configuration

```yaml
sourcing:
  india_hiring_signals:
    enabled: true
    max_queries_per_company: 3
    max_results_per_query: 5
    stop_after_first_match: true
```

No additional API key is required. The service uses the existing Brave, Tavily, Google CSE, or other configured public-search client.

Naukri Resdex and Foundit Resume Database Access are separate paid recruiter products. They can be integrated later through authorized access, but they are not part of this public-source implementation.
