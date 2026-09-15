# Day 3 notes

- Summarization repair rate: 0/12
- Extraction repair rate: 0/12
- Example leakage count: 0
- Citation-existence failure count: 0

The model often left `value` off absent fields, so objects like `{"status": "absent"}` failed the schema. I spelled out that absent fields must be `{"value": null, "status": "absent", "citation": null}`, and the first response then validated without a repair.
