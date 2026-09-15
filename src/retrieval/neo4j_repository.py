"""Neo4j repository for vector, full-text and provenance-aware graph retrieval.

This is the only storage adapter used by the selected Zone 2 backend.  It keeps
all identifiers and query values parameterized; only validated index names and
the bounded traversal depth are interpolated into Cypher because Neo4j does
not support parameters for those syntax positions.

The repository deliberately accepts the typed ``ParsedDocument`` and
``ContentChunk`` contracts from Zone 1.  It never receives LLM-generated text
and never rewrites chunk content.  Batch writes use ``UNWIND`` so ingestion
cost is proportional to batches instead of one network round-trip per node.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

from neo4j import Driver, GraphDatabase

from src.core.config import logger, settings
from src.core.exceptions import GraphDBError, VectorDBError
from src.ingestion.parser.models import ContentChunk, ParsedDocument
from src.retrieval.embeddings import EmbeddingProvider


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Neo4jRepository:
    """Manage the Neo4j storage boundary for chunks and graph context."""

    def __init__(
        self,
        *,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        driver: Driver | Any | None = None,
    ) -> None:
        self.uri = uri or settings.neo4j_uri
        self.user = user or settings.neo4j_user
        self.database = database or settings.neo4j_database
        self.vector_index = self._safe_identifier(settings.neo4j_vector_index, "neo4j_vector_index")
        self.fulltext_index = self._safe_identifier(settings.neo4j_fulltext_index, "neo4j_fulltext_index")
        self._owns_driver = driver is None
        self.driver = driver or GraphDatabase.driver(
            self.uri,
            auth=(self.user, password or settings.neo4j_password),
            connection_timeout=settings.neo4j_connection_timeout_seconds,
            max_connection_pool_size=settings.neo4j_max_connection_pool_size,
            telemetry_disabled=True,
        )

    @staticmethod
    def _safe_identifier(value: str, field_name: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise GraphDBError(
                f"Invalid Neo4j identifier configured for {field_name}",
                details={"value": value},
            )
        return value

    @classmethod
    def from_settings(cls) -> "Neo4jRepository":
        """Create a repository using the application settings without connecting yet."""
        return cls()

    def close(self) -> None:
        """Close the owned driver; injected test drivers remain caller-owned."""
        if self._owns_driver:
            self.driver.close()

    def verify_connectivity(self) -> None:
        """Fail with a structured GraphDBError when Bolt is unavailable."""
        try:
            self.driver.verify_connectivity()
        except Exception as exc:
            raise GraphDBError(
                "Neo4j connectivity check failed",
                details={"uri": self.uri, "database": self.database, "error": str(exc)},
            ) from exc

    def ensure_schema(self) -> None:
        """Create idempotent constraints/indexes required by retrieval."""
        statements = [
            # Zone 1 IDs are deterministic from source content. Scope them by
            # workspace so identical bytes in two tenants never overwrite one
            # another's document, chunk, or provenance graph.
            "CREATE CONSTRAINT chunk_scope_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE (c.workspace_id, c.chunk_id) IS UNIQUE",
            "CREATE CONSTRAINT document_scope_unique IF NOT EXISTS FOR (d:Document) REQUIRE (d.workspace_id, d.document_id) IS UNIQUE",
            "CREATE INDEX chunk_workspace_index IF NOT EXISTS FOR (c:Chunk) ON (c.workspace_id)",
            f"CREATE VECTOR INDEX {self.vector_index} IF NOT EXISTS FOR (c:Chunk) ON c.embedding OPTIONS {{indexConfig: {{`vector.dimensions`: {settings.embedding_dimension}, `vector.similarity_function`: 'cosine'}}}}",
            f"CREATE FULLTEXT INDEX {self.fulltext_index} IF NOT EXISTS FOR (c:Chunk) ON EACH [c.content]",
        ]
        try:
            with self.driver.session(database=self.database) as session:
                existing_vector_indexes = session.run(
                    """
                    SHOW VECTOR INDEXES
                    YIELD name, options, entityType, labelsOrTypes, properties
                    RETURN name, options, entityType, labelsOrTypes, properties
                    """
                )
                for index in existing_vector_indexes:
                    index_config = (index.get("options") or {}).get("indexConfig") or {}
                    dimensions = index_config.get("vector.dimensions")
                    targets_chunk_embedding = (
                        index.get("entityType") == "NODE"
                        and "Chunk" in (index.get("labelsOrTypes") or [])
                        and (index.get("properties") or []) == ["embedding"]
                    )
                    if targets_chunk_embedding and dimensions != settings.embedding_dimension:
                        index_name = self._safe_identifier(
                            str(index["name"]),
                            "existing_vector_index",
                        )
                        logger.warning(
                            f"Rebuilding incompatible Neo4j vector index {index_name}: "
                            f"dimensions={dimensions}, expected={settings.embedding_dimension}"
                        )
                        session.run(f"DROP INDEX {index_name} IF EXISTS").consume()

                for statement in statements:
                    session.run(statement).consume()
        except Exception as exc:
            raise GraphDBError(
                "Neo4j schema initialization failed",
                details={"database": self.database, "error": str(exc)},
            ) from exc

    def prepare(self) -> None:
        """Verify the connection and optionally initialize the configured schema."""
        self.verify_connectivity()
        if settings.neo4j_auto_schema:
            self.ensure_schema()

    def upsert_document(
        self,
        document: ParsedDocument,
        *,
        workspace_id: str,
        source_path: str,
        chunks: Sequence[ContentChunk],
        embedding_provider: EmbeddingProvider | None = None,
    ) -> dict[str, int | str]:
        """Upsert one parsed document and its complete lineage in one write transaction.

        ``embedding_provider`` is optional.  When absent, existing
        ``ContentChunk.embedding`` values are preserved and full-text retrieval
        remains available.  No pseudo-embedding is generated.
        """
        chunks = list(chunks)
        embeddings = self._embed_chunks(chunks, embedding_provider)
        rows = [self._chunk_row(chunk, workspace_id, document, source_path, embeddings.get(chunk.chunk_id)) for chunk in chunks]
        elements = [self._element_row(element, document, workspace_id) for element in document.elements]
        sections, section_edges, chunk_sections = self._section_rows(chunks, workspace_id, document.document_id)
        document_row = {
            "document_id": document.document_id,
            "workspace_id": workspace_id,
            "file_name": document.file_name,
            "file_type": document.file_type,
            "source_path": source_path,
            "total_pages": document.total_pages,
            "metadata_json": json.dumps(document.doc_metadata, ensure_ascii=False, default=str),
        }
        try:
            with self.driver.session(database=self.database) as session:
                session.execute_write(
                    self._write_document,
                    document_row,
                    rows,
                    elements,
                    sections,
                    section_edges,
                    chunk_sections,
                )
        except Exception as exc:
            raise GraphDBError(
                "Neo4j document upsert failed",
                details={"document_id": document.document_id, "workspace_id": workspace_id, "error": str(exc)},
            ) from exc
        logger.info(
            f"NEO4J_UPSERT_COMPLETED workspace={workspace_id} document={document.document_id} "
            f"chunks={len(rows)} elements={len(elements)} sections={len(sections)}"
        )
        return {
            "document_id": document.document_id,
            "chunk_count": len(rows),
            "element_count": len(elements),
            "section_count": len(sections),
        }

    @staticmethod
    def _embed_chunks(
        chunks: Sequence[ContentChunk],
        provider: EmbeddingProvider | None,
    ) -> dict[str, list[float] | None]:
        existing = {chunk.chunk_id: chunk.embedding for chunk in chunks}
        if provider is None:
            return existing
        vectors = provider.embed_documents([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise VectorDBError(
                "Embedding provider returned an unexpected number of vectors",
                details={"chunks": len(chunks), "vectors": len(vectors)},
            )
        return {chunk.chunk_id: vector for chunk, vector in zip(chunks, vectors, strict=True)}

    @staticmethod
    def _chunk_row(
        chunk: ContentChunk,
        workspace_id: str,
        document: ParsedDocument,
        source_path: str,
        embedding: list[float] | None,
    ) -> dict[str, Any]:
        metadata = chunk.metadata
        return {
            "chunk_id": chunk.chunk_id,
            "workspace_id": workspace_id,
            "document_id": document.document_id,
            "source_doc": metadata.source_doc,
            "source_path": source_path,
            "content": chunk.content,
            "embedding": embedding,
            "modality": metadata.modality,
            "page_numbers": metadata.page_numbers,
            "element_ids": metadata.element_ids,
            "section_path": metadata.section_path,
            "parent_header": metadata.parent_header,
            "parent_chunk_id": metadata.parent_chunk_id,
            "chunk_strategy": metadata.chunk_strategy,
            "chunk_index": metadata.chunk_index,
            "has_table": metadata.has_table,
            "has_image": metadata.has_image,
            "language": metadata.language,
            "table_markdown": chunk.table_markdown,
            "vlm_caption": chunk.vlm_caption,
            "code_snippet": chunk.code_snippet,
            "metadata_json": json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, default=str),
        }

    @staticmethod
    def _element_row(element: Any, document: ParsedDocument, workspace_id: str) -> dict[str, Any]:
        metadata = element.metadata
        bbox = metadata.bounding_box
        return {
            "element_id": element.element_id,
            "element_key": f"{workspace_id}:{document.document_id}:{element.element_id}",
            "workspace_id": workspace_id,
            "document_id": document.document_id,
            "content": element.content,
            "element_type": metadata.element_type,
            "page_number": metadata.page_number,
            "element_index": metadata.element_index,
            "confidence": metadata.confidence,
            "parent_header": metadata.parent_header,
            "section_path": metadata.section_path,
            "bbox": [bbox.x1, bbox.y1, bbox.x2, bbox.y2] if bbox else None,
            "extra_json": json.dumps(metadata.extra, ensure_ascii=False, default=str),
        }

    @staticmethod
    def _section_rows(
        chunks: Sequence[ContentChunk],
        workspace_id: str,
        document_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, list[str]]]:
        sections: dict[str, dict[str, Any]] = {}
        edges: set[tuple[str, str]] = set()
        chunk_sections: dict[str, list[str]] = {}
        for chunk in chunks:
            previous_id: str | None = None
            ids: list[str] = []
            for level, title in enumerate(chunk.metadata.section_path, start=1):
                path = chunk.metadata.section_path[:level]
                section_id = "section-" + hashlib.sha256(
                    f"{workspace_id}\0{document_id}\0{' > '.join(path)}".encode("utf-8")
                ).hexdigest()[:16]
                sections.setdefault(
                    section_id,
                    {
                        "section_id": section_id,
                        "workspace_id": workspace_id,
                        "document_id": document_id,
                        "title": title,
                        "path": path,
                        "level": level,
                    },
                )
                ids.append(section_id)
                if previous_id:
                    edges.add((previous_id, section_id))
                previous_id = section_id
            if ids:
                chunk_sections[chunk.chunk_id] = ids
        return list(sections.values()), [
            {"workspace_id": workspace_id, "parent_id": parent_id, "child_id": child_id}
            for parent_id, child_id in sorted(edges)
        ], chunk_sections

    @staticmethod
    def _write_document(
        tx: Any,
        document_row: dict[str, Any],
        chunks: list[dict[str, Any]],
        elements: list[dict[str, Any]],
        sections: list[dict[str, Any]],
        section_edges: list[dict[str, str]],
        chunk_sections: dict[str, list[str]],
    ) -> None:
        document_id = document_row["document_id"]
        workspace_id = document_row["workspace_id"]
        chunk_ids = [row["chunk_id"] for row in chunks]
        tx.run(
            """
            MATCH (d:Document {document_id: $document_id, workspace_id: $workspace_id})
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(old:Chunk)
            WHERE NOT old.chunk_id IN $chunk_ids
            DETACH DELETE old
            """,
            document_id=document_id,
            workspace_id=workspace_id,
            chunk_ids=chunk_ids,
        ).consume()
        tx.run(
            """
            MERGE (d:Document {workspace_id: $workspace_id, document_id: $document_id})
            SET d.workspace_id = $workspace_id,
                d.file_name = $file_name,
                d.file_type = $file_type,
                d.source_path = $source_path,
                d.total_pages = $total_pages,
                d.metadata_json = $metadata_json,
                d.updated_at = datetime()
            """,
            **document_row,
        ).consume()
        tx.run(
            """
            MATCH (d:Document {document_id: $document_id, workspace_id: $workspace_id})-[:HAS_ELEMENT]->(old:Element)
            WHERE NOT old.element_id IN $element_ids
            DETACH DELETE old
            """,
            document_id=document_id,
            workspace_id=workspace_id,
            element_ids=[row["element_id"] for row in elements],
        ).consume()
        tx.run(
            """
            MATCH (d:Document {document_id: $document_id, workspace_id: $workspace_id})-[:HAS_SECTION]->(old:Section)
            WHERE NOT old.section_id IN $section_ids
            DETACH DELETE old
            """,
            document_id=document_id,
            workspace_id=workspace_id,
            section_ids=[row["section_id"] for row in sections],
        ).consume()
        if elements:
            tx.run(
                """
                UNWIND $elements AS row
                MATCH (d:Document {document_id: row.document_id, workspace_id: row.workspace_id})
                MERGE (e:Element {workspace_id: row.workspace_id, element_key: row.element_key})
                SET e.element_id = row.element_id,
                    e.workspace_id = row.workspace_id,
                    e.document_id = row.document_id,
                    e.content = row.content,
                    e.element_type = row.element_type,
                    e.page_number = row.page_number,
                    e.element_index = row.element_index,
                    e.confidence = row.confidence,
                    e.parent_header = row.parent_header,
                    e.section_path = row.section_path,
                    e.bbox = row.bbox,
                    e.extra_json = row.extra_json
                MERGE (d)-[:HAS_ELEMENT]->(e)
                """,
                elements=elements,
            ).consume()
        if sections:
            tx.run(
                """
                UNWIND $sections AS row
                MATCH (d:Document {document_id: row.document_id, workspace_id: row.workspace_id})
                MERGE (s:Section {workspace_id: row.workspace_id, section_id: row.section_id})
                SET s.workspace_id = row.workspace_id,
                    s.document_id = row.document_id,
                    s.title = row.title,
                    s.path = row.path,
                    s.level = row.level
                MERGE (d)-[:HAS_SECTION]->(s)
                """,
                sections=sections,
            ).consume()
            tx.run(
                """
                MATCH (d:Document {document_id: $document_id, workspace_id: $workspace_id})-[:HAS_SECTION]->(s:Section)
                OPTIONAL MATCH (s)-[r:PARENT_SECTION]-()
                DELETE r
                """,
                document_id=document_id,
                workspace_id=workspace_id,
            ).consume()
            if section_edges:
                tx.run(
                    """
                    UNWIND $edges AS row
                    MATCH (parent:Section {workspace_id: row.workspace_id, section_id: row.parent_id})
                    MATCH (child:Section {workspace_id: row.workspace_id, section_id: row.child_id})
                    MERGE (parent)-[:PARENT_SECTION]->(child)
                    """,
                    edges=section_edges,
                ).consume()
        if chunks:
            tx.run(
                """
                UNWIND $chunks AS row
                MERGE (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                SET c.workspace_id = row.workspace_id,
                    c.document_id = row.document_id,
                    c.source_doc = row.source_doc,
                    c.source_path = row.source_path,
                    c.content = row.content,
                    c.embedding = row.embedding,
                    c.modality = row.modality,
                    c.page_numbers = row.page_numbers,
                    c.element_ids = row.element_ids,
                    c.section_path = row.section_path,
                    c.parent_header = row.parent_header,
                    c.parent_chunk_id = row.parent_chunk_id,
                    c.chunk_strategy = row.chunk_strategy,
                    c.chunk_index = row.chunk_index,
                    c.has_table = row.has_table,
                    c.has_image = row.has_image,
                    c.language = row.language,
                    c.table_markdown = row.table_markdown,
                    c.vlm_caption = row.vlm_caption,
                    c.code_snippet = row.code_snippet,
                    c.metadata_json = row.metadata_json,
                    c.updated_at = datetime()
                """,
                chunks=chunks,
            ).consume()
            tx.run(
                """
                UNWIND $chunks AS row
                MATCH (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                OPTIONAL MATCH (c)-[r:SOURCED_FROM]->()
                DELETE r
                """,
                chunks=chunks,
            ).consume()
            tx.run(
                """
                UNWIND $chunks AS row
                MATCH (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                OPTIONAL MATCH (c)-[r:IN_SECTION]->()
                DELETE r
                """,
                chunks=chunks,
            ).consume()
            tx.run(
                """
                MATCH (d:Document {document_id: $document_id, workspace_id: $workspace_id})-[:HAS_CHUNK]->(c:Chunk)
                OPTIONAL MATCH (c)-[r:PARENT_OF]-()
                DELETE r
                """,
                document_id=document_id,
                workspace_id=workspace_id,
            ).consume()
            tx.run(
                """
                UNWIND $chunks AS row
                MATCH (d:Document {document_id: row.document_id, workspace_id: row.workspace_id})
                MATCH (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                MERGE (d)-[:HAS_CHUNK]->(c)
                """,
                chunks=chunks,
            ).consume()
            tx.run(
                """
                UNWIND $chunks AS row
                MATCH (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                UNWIND row.element_ids AS element_id
                MATCH (e:Element {workspace_id: row.workspace_id, element_id: element_id, document_id: row.document_id})
                MERGE (c)-[:SOURCED_FROM]->(e)
                """,
                chunks=chunks,
            ).consume()
            if chunk_sections:
                section_links = [
                    {"workspace_id": workspace_id, "chunk_id": chunk_id, "section_id": section_id}
                    for chunk_id, section_ids in chunk_sections.items()
                    for section_id in section_ids
                ]
                tx.run(
                    """
                    UNWIND $links AS row
                    MATCH (c:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                    MATCH (s:Section {workspace_id: row.workspace_id, section_id: row.section_id})
                    MERGE (c)-[:IN_SECTION]->(s)
                    """,
                    links=section_links,
                ).consume()
            tx.run(
                """
                UNWIND $chunks AS row
                WITH row
                WHERE row.parent_chunk_id IS NOT NULL
                MATCH (parent:Chunk {workspace_id: row.workspace_id, chunk_id: row.parent_chunk_id})
                MATCH (child:Chunk {workspace_id: row.workspace_id, chunk_id: row.chunk_id})
                MERGE (parent)-[:PARENT_OF]->(child)
                """,
                chunks=chunks,
            ).consume()

    def search_vector(
        self,
        *,
        workspace_id: str,
        query_embedding: Sequence[float],
        top_k: int | None = None,
        file_paths: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the Neo4j vector index and apply workspace/path isolation."""
        requested = top_k or settings.retrieval_top_k
        candidate_limit = requested * settings.neo4j_vector_oversampling
        if settings.neo4j_vector_query_mode == "search":
            query = f"""
            MATCH (node:Chunk)
            SEARCH node IN (
                VECTOR INDEX {self.vector_index}
                FOR $query_embedding
                LIMIT $candidate_limit
            ) SCORE AS score
            WHERE node.workspace_id = $workspace_id
              AND (size($file_paths) = 0 OR node.source_path IN $file_paths)
            RETURN node, score
            ORDER BY score DESC
            LIMIT $requested
            """
        else:
            query = f"""
            CALL db.index.vector.queryNodes($index_name, $candidate_limit, $query_embedding)
            YIELD node, score
            WHERE node.workspace_id = $workspace_id
              AND (size($file_paths) = 0 OR node.source_path IN $file_paths)
            RETURN node, score
            ORDER BY score DESC
            LIMIT $requested
            """
        rows = self._read(
            query,
            index_name=self.vector_index,
            candidate_limit=candidate_limit,
            query_embedding=list(query_embedding),
            workspace_id=workspace_id,
            file_paths=list(file_paths or []),
            requested=requested,
        )
        return [self._chunk_result(row["node"], row["score"], "vector_dense") for row in rows]

    def search_fulltext(
        self,
        *,
        workspace_id: str,
        query: str,
        top_k: int | None = None,
        file_paths: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search Neo4j full-text index; this is the lexical/BM25 channel."""
        requested = top_k or settings.retrieval_top_k
        candidate_limit = requested * settings.neo4j_vector_oversampling
        rows = self._read(
            f"""
            CALL db.index.fulltext.queryNodes($index_name, $search_text, {{limit: $candidate_limit}})
            YIELD node, score
            WHERE node.workspace_id = $workspace_id
              AND (size($file_paths) = 0 OR node.source_path IN $file_paths)
            RETURN node, score
            ORDER BY score DESC
            LIMIT $requested
            """,
            index_name=self.fulltext_index,
            search_text=query,
            candidate_limit=candidate_limit,
            requested=requested,
            workspace_id=workspace_id,
            file_paths=list(file_paths or []),
        )
        return [self._chunk_result(row["node"], row["score"], "bm25") for row in rows]

    def search_hybrid(
        self,
        *,
        workspace_id: str,
        keyword_query: str,
        query_embedding: Sequence[float] | None = None,
        top_k: int | None = None,
        file_paths: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fuse dense and full-text ranks using reciprocal rank fusion."""
        requested = top_k or settings.retrieval_top_k
        dense = self.search_vector(
            workspace_id=workspace_id,
            query_embedding=query_embedding,
            top_k=requested,
            file_paths=file_paths,
        ) if query_embedding is not None else []
        lexical = self.search_fulltext(
            workspace_id=workspace_id,
            query=keyword_query,
            top_k=requested,
            file_paths=file_paths,
        )
        by_id: dict[str, dict[str, Any]] = {}
        dense_scores: dict[str, float] = {}
        lexical_scores: dict[str, float] = {}
        for rank, item in enumerate(dense, start=1):
            chunk_id = item["chunk_id"]
            by_id.setdefault(chunk_id, item)
            dense_scores[chunk_id] = float(item.get("score", 0.0))
            by_id[chunk_id]["metadata"]["dense_rank"] = rank
        for rank, item in enumerate(lexical, start=1):
            chunk_id = item["chunk_id"]
            by_id.setdefault(chunk_id, item)
            lexical_scores[chunk_id] = float(item.get("score", 0.0))
            by_id[chunk_id]["metadata"]["bm25_rank"] = rank
        for chunk_id, item in by_id.items():
            rrf_score = 0.0
            if chunk_id in dense_scores:
                rrf_score += 1.0 / (settings.neo4j_rrf_k + item["metadata"]["dense_rank"])
            if chunk_id in lexical_scores:
                rrf_score += 1.0 / (settings.neo4j_rrf_k + item["metadata"]["bm25_rank"])
            item["score"] = rrf_score
            item["source_type"] = "hybrid" if chunk_id in dense_scores and chunk_id in lexical_scores else "vector_dense" if chunk_id in dense_scores else "bm25"
            item["metadata"].update({"dense_score": dense_scores.get(chunk_id), "bm25_score": lexical_scores.get(chunk_id), "rrf_score": rrf_score})
        return sorted(by_id.values(), key=lambda item: item["score"], reverse=True)[:requested]

    def get_chunk(self, *, workspace_id: str, chunk_id: str) -> dict[str, Any] | None:
        """Resolve one exact chunk for an evidence request."""
        rows = self._read(
            "MATCH (c:Chunk {chunk_id: $chunk_id, workspace_id: $workspace_id}) RETURN c LIMIT 1",
            chunk_id=chunk_id,
            workspace_id=workspace_id,
        )
        return self._chunk_result(rows[0]["c"], 1.0, "hybrid") if rows else None

    def query_graph(
        self,
        *,
        workspace_id: str,
        entity_query: str,
        max_hops: int = 2,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Expand relationships around chunks matching a graph/entity query."""
        bounded_hops = max(1, min(int(max_hops), 5))
        bounded_limit = limit or settings.retrieval_top_k
        rows = self._read(
            f"""
            CALL db.index.fulltext.queryNodes($index_name, $entity_query, {{limit: $candidate_limit}})
            YIELD node, score
            WHERE node.workspace_id = $workspace_id
            MATCH path = (node)-[*1..{bounded_hops}]-(target)
            WHERE target.workspace_id = $workspace_id
            WITH node, target, relationships(path) AS rels, score
            UNWIND rels AS rel
            RETURN DISTINCT
              coalesce(node.chunk_id, node.document_id, node.section_id, node.element_id) AS source_node,
              type(rel) AS relationship,
              coalesce(target.chunk_id, target.document_id, target.section_id, target.element_id) AS target_node,
              score
            LIMIT $bounded_limit
            """,
            index_name=self.fulltext_index,
            entity_query=entity_query,
            candidate_limit=bounded_limit * settings.neo4j_vector_oversampling,
            workspace_id=workspace_id,
            bounded_limit=bounded_limit,
        )
        return [
            {
                "source_node": row["source_node"],
                "relationship": row["relationship"],
                "target_node": row["target_node"],
                "properties": {"match_score": row.get("score", 0.0)},
            }
            for row in rows
        ]

    def workspace_graph(self, *, workspace_id: str, node_limit: int = 120, edge_limit: int = 240) -> dict[str, Any]:
        """Return a bounded provenance graph for the workspace UI."""
        rows = self._read(
            """
            MATCH (d:Document {workspace_id: $workspace_id})
            OPTIONAL MATCH (d)-[r:HAS_CHUNK]->(c:Chunk)
            RETURN d, r, c
            LIMIT $edge_limit
            """,
            workspace_id=workspace_id,
            edge_limit=edge_limit,
        )
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for row in rows:
            document = dict(row["d"])
            document_id = str(document["document_id"])
            nodes[document_id] = {"id": document_id, "degree": 0, "attributes": {"name": document.get("file_name"), "source_path": document.get("source_path"), "type": "document"}}
            chunk = row.get("c")
            if chunk:
                chunk_data = dict(chunk)
                chunk_id = str(chunk_data["chunk_id"])
                nodes[chunk_id] = {"id": chunk_id, "degree": 0, "attributes": {"source_path": chunk_data.get("source_path"), "type": "chunk", "section_path": chunk_data.get("section_path", [])}}
                nodes[document_id]["degree"] += 1
                nodes[chunk_id]["degree"] += 1
                edges.append({"source": document_id, "target": chunk_id, "attributes": {"relationship": "HAS_CHUNK"}})
        bounded_nodes = list(nodes.values())[:node_limit]
        bounded_ids = {node["id"] for node in bounded_nodes}
        bounded_edges = [edge for edge in edges if edge["source"] in bounded_ids and edge["target"] in bounded_ids][:edge_limit]
        return {
            "workspace_id": workspace_id,
            "storage": "neo4j",
            "format": "json",
            "graphml_path": "",
            "node_count": len(bounded_nodes),
            "edge_count": len(bounded_edges),
            "is_directed": True,
            "is_multigraph": False,
            "density": 0.0,
            "connected_components": len(bounded_nodes),
            "limits": {"nodes": node_limit, "edges": edge_limit, "include_attributes": True},
            "top_degree_nodes": sorted(
                [{"id": node["id"], "degree": node["degree"]} for node in bounded_nodes],
                key=lambda item: item["degree"],
                reverse=True,
            )[:10],
            "nodes": bounded_nodes,
            "edges": bounded_edges,
        }

    def _read(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        try:
            with self.driver.session(database=self.database) as session:
                return [record.data() for record in session.run(query, **parameters)]
        except Exception as exc:
            raise GraphDBError(
                "Neo4j read query failed",
                details={"database": self.database, "error": str(exc)},
            ) from exc

    @staticmethod
    def _chunk_result(node: Mapping[str, Any] | Any, score: float, source_type: str) -> dict[str, Any]:
        values = dict(node)
        metadata: dict[str, Any]
        try:
            metadata = json.loads(values.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        metadata.update(
            {
                "workspace_id": values.get("workspace_id"),
                "source_path": values.get("source_path"),
                "page_numbers": values.get("page_numbers", []),
                "element_ids": values.get("element_ids", []),
                "section_path": values.get("section_path", []),
                "parent_chunk_id": values.get("parent_chunk_id"),
            }
        )
        return {
            "chunk_id": values.get("chunk_id"),
            "content": values.get("content", ""),
            "modality": values.get("modality", "text"),
            "source_type": source_type,
            "source_doc": values.get("source_doc") or values.get("source_path") or "",
            "score": float(score),
            "metadata": metadata,
            "vlm_caption": values.get("vlm_caption"),
            "document_id": values.get("document_id"),
            "page_numbers": values.get("page_numbers", []),
            "element_ids": values.get("element_ids", []),
            "section_path": values.get("section_path", []),
            "parent_chunk_id": values.get("parent_chunk_id"),
        }


__all__ = ["Neo4jRepository"]
