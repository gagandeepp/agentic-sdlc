"""Turn metered proxy usage into run events."""

from app.events.bus import EventBus
from app.events.models import AgentUsagePayload, BudgetUpdatedPayload
from app.llm.proxy import UsageRecord, UsageSink
from app.llm.registry import Grant


def bus_usage_sink(bus: EventBus) -> UsageSink:
    async def sink(grant: Grant, record: UsageRecord) -> None:
        usage = record.usage
        await bus.emit(
            grant.run_id,
            "agent.usage",
            AgentUsagePayload(
                model=record.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
                context_tokens=record.context_tokens,
                context_max=record.context_max,
                cost_usd=record.cost_usd,
            ),
            phase=grant.phase,
            agent_id=grant.agent_id,
        )
        await bus.emit(
            grant.run_id,
            "budget.updated",
            BudgetUpdatedPayload(spent_usd=record.spent_usd, budget_usd=record.budget_usd),
            phase=grant.phase,
        )

    return sink
