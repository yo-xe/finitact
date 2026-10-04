# Finitact

Finitact is the bounded action-selection layer between a goal-producing agent and an observed interface.

## Language

**Observed candidate**:
An action or target derived from the current interface observation and eligible for selection.
_Avoid_: Generated selector, invented target

**Decision provider**:
A replaceable, low-overhead model that selects among observed candidates.
_Avoid_: Jev when referring to the provider role, planner

**Action adapter**:
A boundary that observes one interface type and executes selected actions against it.
_Avoid_: Driver, backend

**Outcome verifier**:
An independent check of task success; selecting `DONE` is not verification.
_Avoid_: DONE check

**Click label constraint**:
An optional per-goal delivery policy that permits clicking only one observed candidate whose label exactly matches
the declared label. It restricts delivery and does not verify the outcome.
_Avoid_: Target verifier, goal parser
