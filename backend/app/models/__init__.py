# ORM model registry.
#
# Import the shared declarative ``Base`` and re-export it so Alembic's
# ``migrations/env.py`` binds autogeneration to a single metadata object.
# Model modules are imported here so their tables register on
# ``Base.metadata`` before autogenerate / create_all runs.
from __future__ import annotations

from app.core.database import Base
from app.models.analysis import (
    BrandMention,
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditEvent,
    AuditPromptSnapshot,
    AuditTask,
    ProviderAttempt,
    RawResponseArtifact,
)
from app.models.brand import (
    Brand,
    BrandAlias,
    BrandProfile,
    BrandProfileSuggestion,
    Competitor,
    OwnedDomain,
    UnintendedDomain,
)
from app.models.content import ContentGeneration, ContentGenerationAttempt
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationImportArtifact,
    IntegrationMetricRow,
    IntegrationOAuthGrant,
    IntegrationOAuthState,
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet, Topic
from app.models.provider import (
    DiscoveryModelConfig,
    ProviderConnection,
    ProviderConnectionTest,
    ProviderRoute,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlEvent,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteFetchAttempt,
    SiteHealthProfile,
    SiteHealthSnapshot,
    SiteIssue,
    SiteLinkReference,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
    WorkspaceSiteHealthEntitlement,
)
from app.models.traffic import TrafficPageStat, TrafficQueryStat, TrafficSnapshot
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

__all__ = [
    "Audit",
    "AuditEngineSnapshot",
    "AuditEvent",
    "AuditPromptSnapshot",
    "AuditTask",
    "Base",
    "Brand",
    "BrandAlias",
    "BrandProfile",
    "BrandProfileSuggestion",
    "BrandMention",
    "Citation",
    "Competitor",
    "CompetitorMention",
    "ContentGeneration",
    "ContentGenerationAttempt",
    "DiscoveryModelConfig",
    "IntegrationConnection",
    "IntegrationEvent",
    "IntegrationImportArtifact",
    "IntegrationMetricRow",
    "IntegrationOAuthGrant",
    "IntegrationOAuthState",
    "IntegrationPropertyMapping",
    "IntegrationSyncRun",
    "MetricSnapshot",
    "OwnedDomain",
    "Project",
    "Prompt",
    "PromptSet",
    "ProviderAttempt",
    "ProviderConnection",
    "ProviderConnectionTest",
    "ProviderRoute",
    "RawResponseArtifact",
    "ResponseAnalysis",
    "MonitoredSiteUrl",
    "SiteCrawl",
    "SiteCrawlEvent",
    "SiteCrawlTask",
    "SiteFetchArtifact",
    "SiteFetchAttempt",
    "SiteHealthProfile",
    "SiteHealthSnapshot",
    "SiteIssue",
    "SiteLinkReference",
    "SitePageAnalysis",
    "SiteRuleEvaluation",
    "SiteUrl",
    "SiteUrlObservation",
    "WorkspaceSiteHealthEntitlement",
    "Topic",
    "TrafficPageStat",
    "TrafficQueryStat",
    "TrafficSnapshot",
    "UnintendedDomain",
    "User",
    "Workspace",
    "WorkspaceMember",
]
