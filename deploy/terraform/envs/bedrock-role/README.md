# The cross-account Bedrock role (optional)

For an account whose Bedrock daily token quota is 0 (new accounts: Service Quotas `L-D2912E70` and siblings, non-adjustable), the web chat agent can call the model as a role in another account — the only call that leaves the stack's account. Create the role **in the other account** once, then set `bedrock_assume_role_arn` in `aws.tfvars` and `make deploy`.

`trust.json` trusts the stack's *account* and restricts it with an `aws:PrincipalArn` condition to the agent role (`<name>-agentcore-agent`) — a string match, so the role may be destroyed and recreated by `make down` / `make deploy` without touching this policy (naming the role as the principal would pin it to one incarnation). `policy.json` allows only the two invoke actions. Replace the two account ids with yours.

```bash
# with credentials for the OTHER account (the one with quota):
sed "s/<STACK_ACCOUNT_ID>/<the stack account>/" trust.json > /tmp/trust.json && sed "s/<MODEL_ACCOUNT_ID>/<this account>/" policy.json > /tmp/policy.json
aws iam create-role --role-name ask-the-tower-bedrock --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name ask-the-tower-bedrock --policy-name invoke-model --policy-document file:///tmp/policy.json
# model access for the Nova models must be enabled in that account's Bedrock console as well
```

Remove: `aws iam delete-role-policy … && aws iam delete-role --role-name ask-the-tower-bedrock`, and clear `bedrock_assume_role_arn`.
