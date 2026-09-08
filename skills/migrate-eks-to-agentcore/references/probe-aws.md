# Node C — quotas, region availability, prices

Needs S2 **in their account**. Read live, never from recall: several thresholds are adjustable and
accounts differ.

## First: whose account are you in?

```bash
aws sts get-caller-identity
```

Compare it to the customer's account id. **If they differ, most of this node is unavailable**, and
running it anyway produces a confident wrong answer — the worst failure mode here:

- `list-service-quotas` returns **your** applied values, not theirs. Record only
  `list-aws-default-service-quotas` output, tagged `[verified: defaults only]`, and mark every
  applied value `open (wrong account)`.
- Every `describe-vpc-*`, `describe-route-tables` and registry or budget read describes *your*
  infrastructure.
- Region availability and prices are account-independent, so those remain valid.

This matters because everything below insists on applied-vs-default. From outside the account that
instruction actively causes the error it exists to prevent. It is also why `scripts/lens_plan.py`
grants the `aws` access class only when control-plane read **and** `account_is_customers` are both
true.

Downstream: node D gates against defaults and marks `compute_type_assumed` when C is degraded.

## The probes

```bash
# Quotas — defaults, and what this account actually has (they differ)
aws service-quotas list-aws-default-service-quotas --service-code bedrock-agentcore --region <r>
aws service-quotas list-service-quotas --service-code bedrock-agentcore --region <r>

# Region availability — probe; published lists have been stale
aws bedrock-agentcore-control list-agent-runtimes --region <r> --max-results 1

# Node price for the EKS side of the cost model
aws pricing get-products --service-code AmazonEC2 --region us-east-1 \
  --filters "Type=TERM_MATCH,Field=instanceType,Value=<type>" \
            "Type=TERM_MATCH,Field=location,Value=<location>" \
            "Type=TERM_MATCH,Field=operatingSystem,Value=Linux" \
            "Type=TERM_MATCH,Field=tenancy,Value=Shared" \
            "Type=TERM_MATCH,Field=preInstalledSw,Value=NA" \
            "Type=TERM_MATCH,Field=capacitystatus,Value=Used"

# Does the VPC have internet egress? Decides whether endpoint cost is AgentCore-only or shared.
aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc> \
  --query 'RouteTables[].Routes[?DestinationCidrBlock==`0.0.0.0/0`]'
```

Read-only throughout. If the account might be production, say which identity you are using before
calling anything, and do not create, modify or delete.

Two traps worth knowing:

- **`timeout` is not present on macOS.** A probe loop wrapping AWS calls in it fails uniformly and
  looks like "unavailable in every region" — a false negative that produces a wrong blocker.
- **Quotas and prices both vary by region, not just by account.** Which gates that decides, and the
  near-name collisions between runtime and Gateway quotas, are in
  [constraints.md](constraints.md); the price side is in [cost-model.md](cost-model.md).
