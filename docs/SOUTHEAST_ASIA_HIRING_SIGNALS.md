# Southeast Asia Hiring Signals

The sourcing pipeline automatically enables this strategy for company seeds in Singapore, Malaysia, Thailand, Indonesia, Vietnam, and the Philippines. Sales users do not select a region mode or operate recruitment websites manually.

## Country Routing

| Country | Primary public hiring sources |
| --- | --- |
| Singapore | JobStreet, MyCareersFuture, Glints |
| Malaysia | JobStreet, MyFutureJobs, Maukerja |
| Thailand | JobsDB, JobThai, JobBKK |
| Indonesia | JobStreet, Glints, Dealls, Kalibrr |
| Vietnam | TopCV, VietnamWorks, CareerViet, Glints |
| Philippines | JobStreet, Kalibrr, Glints, OnlineJobs.ph |

Luxury accounts in Singapore, Malaysia, and Thailand may also use public Luxury Careers results. Platforms are queried in priority order and the default strategy stops after the first reliable match, so public-search credits remain bounded.

## Automatic Processing

1. Detect the country from imported country/location fields.
2. Detect whether the account is luxury, consumer electronics, automotive, or general retail.
3. Select the country's public hiring platforms and local search language.
4. Keep only job pages or company job-list pages that match the imported company.
5. Reject closed, expired, review, and unrelated pages.
6. Recognize management and frontline retail roles in English, Malay, Indonesian, Thai, and Vietnamese.
7. Preserve platform, URL, role, reported opening volume, and an explainable expansion score.
8. Feed the evidence into account priority, `why now`, customer profile, and personalized email context.

The strategy does not collect candidate resumes or private personal data. Provider failure never blocks normal domain resolution, LinkedIn decision-maker search, email discovery, or draft generation.

## Configuration

```yaml
sourcing:
  southeast_asia_hiring_signals:
    enabled: true
    max_queries_per_company: 3
    max_results_per_query: 5
    stop_after_first_match: true
```

The default path reuses the existing Brave, Tavily, or Google public-search provider. It requires no additional API key and adds no frontend controls.
