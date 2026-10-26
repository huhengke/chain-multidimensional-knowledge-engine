"""M4 知识认知脑（KnowledgeBrain）。

五层导入管道 parse/segment/annotate/gate/publish，六件套统一证据契约，
受管运行时适配边界 ``KnowledgeRuntime``（当前使用内建确定性实现，WeKnora 尚未接入）。
"""

from brains.knowledge.src.brain import KnowledgeBrain
from brains.knowledge.src.contract import (
    CanonicalDocument,
    KnowledgeEvidence,
    KnowledgeReasoningResult,
    KnowledgeRoute,
    KnowledgeSource,
    KnowledgeUnit,
    UnitLocator,
)
from brains.knowledge.src.ontology import (
    Annotation,
    Concept,
    OntologyStore,
    Relation,
)
from brains.knowledge.src.pipeline import (
    ConflictRecord,
    DocumentPublication,
    GateRecord,
    KnowledgePipeline,
    KnowledgeStore,
)
from brains.knowledge.src.reasoning import ProfessionalReasoner, ReasoningRule
from brains.knowledge.src.runtime_adapter import (
    BuiltinKnowledgeRuntime,
    ManagedWeKnoraRuntime,
    KnowledgeInput,
    KnowledgeRuntime,
    ParsedContent,
    RuntimeMetadata,
    RuntimeUnavailableError,
    Segment,
)
from brains.knowledge.src.workbench import (
    CrossBrainLink,
    KnowledgeClusterProjection,
    KnowledgeImportJob,
    KnowledgeIndexVersion,
    KnowledgeWorkbenchService,
)

__all__ = [
    "KnowledgeBrain",
    "KnowledgePipeline",
    "KnowledgeStore",
    "KnowledgeRuntime",
    "BuiltinKnowledgeRuntime",
    "ManagedWeKnoraRuntime",
    "RuntimeUnavailableError",
    "KnowledgeInput",
    "RuntimeMetadata",
    "ParsedContent",
    "Segment",
    "KnowledgeSource",
    "CanonicalDocument",
    "KnowledgeUnit",
    "KnowledgeEvidence",
    "KnowledgeRoute",
    "KnowledgeReasoningResult",
    "UnitLocator",
    "OntologyStore",
    "Concept",
    "Relation",
    "Annotation",
    "ProfessionalReasoner",
    "ReasoningRule",
    "DocumentPublication",
    "GateRecord",
    "ConflictRecord",
    "KnowledgeWorkbenchService",
    "KnowledgeImportJob",
    "KnowledgeIndexVersion",
    "KnowledgeClusterProjection",
    "CrossBrainLink",
]
