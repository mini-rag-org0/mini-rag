from enum import Enum

class VecotrDBEnums(Enum):
    QDRANT = "QDRANT"
    PGVECTOR = "PGVECTOR"


class DistnaceMethodEnum(Enum):
    COSINE = "cosine"
    DOT = "dot"

class PgVectorTableSchemeEnums(Enum):
    ID = "id"
    TEXT = "text"
    VECTOR = "vector"
    CHUNK_ID = "chunk_id"
    METADATA = "metadata"
    _PREFIX = "pgvector"

class PgVectorDistnaceMethodEnum(Enum):
    COSINE = "vector_cosine_ops"
    DOT = "vector_ip_ops"

class PgVectorIndexTypeEnum(Enum):
    HNSW = "hnsw"
    IVFFLAT = "ivfflat"