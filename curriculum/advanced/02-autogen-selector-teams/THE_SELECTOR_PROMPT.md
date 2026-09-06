# Deep Dive: Define the Selector Contract

A good `selector_prompt` helps, but prompt wording is only one part of routing correctness. Treat the selector as an untrusted proposal generator inside an application-owned contract.

## Contract inputs

| Input | Purpose |
|---|---|
| Goal | keeps selection tied to the incident outcome |
| Evidence gaps | identifies work that can materially advance state |
| Eligible speakers | bounds the output space mechanically |
| Disallowed transitions | makes premature analysis/review visible |
| Last material change | distinguishes progress from repetition |
| Remaining budget | supports abstention before damage grows |
| Termination state | prevents another call after stop/cancel |

Raw messages can support explanation, but they must not override tenant, incident, capability, budget, evidence, or termination state.

## Bad and better prompts

Bad:

> Choose the next speaker based on the conversation.

Better:

> Choose exactly one name from the supplied eligible speakers. Select a specialist that can close a named unresolved evidence gap. Do not invent speakers, expand authority, route to analysis before evidence sufficiency, or route to review before a candidate exists. Return only the requested structured fields.

Even the better wording does not enforce policy. The application still computes candidates, parses a strict `SelectorDecision`, and calls `validate_selector_decision()` before one worker turn.

## Set-valued correctness

Suppose health and deployment evidence are missing. Both `ObservabilityAgent` and `DeploymentAgent` may be valid. A label that permits only one would punish safe routing and encourage brittle evaluation.

Use `eligible_agents` for the application-computed candidate set and `valid_next_agents` for the subset accepted by the labelled case. Score whether the prediction belongs to the valid set.

## Confidence and abstention

If the candidate set is empty, stop before calling AutoGen's selector because `candidate_func` requires at least one candidate. Record `NO_ELIGIBLE_SPEAKER` and then apply the evidence/failure policy.

If candidates exist but the selector cannot choose reliably, return `AMBIGUOUS_SELECTION`. Do not force a destination just to continue the chat.

## Structured output

Prefer a structured model response where supported. Otherwise accept a minimal string or JSON object, parse it strictly, reject unexpected fields, and validate the resulting typed decision. A name in free-form prose is not a trustworthy control message.

## AutoGen mapping

In AutoGen AgentChat 0.7.5, `candidate_func` filters participant names, `selector_prompt` shapes the choice, and `selector_func` can override model selection but is not serialized with team configuration. The application should still validate the selected source before accepting its artifact.

The prompt is not the router. The prompt, candidate filter, typed state, validation, budgets, termination, artifact gates, and evaluation together form the routing system.
