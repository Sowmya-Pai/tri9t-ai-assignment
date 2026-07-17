"""
Database models for document versioning and test case generation system.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
import uuid

from sqlalchemy import (
    Column, String, Text, DateTime, Integer, Boolean, 
    ForeignKey, JSON, Enum as SQLEnum, Float, Index
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class ChangeType(str, Enum):
    """How a node changed between versions"""
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    DELETED = "deleted"
    CREATED = "created"
    MOVED = "moved"  # Structural position changed


class StalenessLevel(str, Enum):
    """How stale is generated content?"""
    FRESH = "fresh"  # Content matches exactly
    POSSIBLY_STALE = "possibly_stale"  # >50% similar but not exact
    DEFINITELY_STALE = "definitely_stale"  # Content significantly changed
    UNKNOWN = "unknown"  # Can't determine (no comparison possible)


class LLMProvider(str, Enum):
    """Which LLM provider generated the output"""
    GROQ = "groq"
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Priority(str, Enum):
    """Test case priority levels"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Document(Base):
    """Root document (e.g., "CT-200 Manual")"""
    __tablename__ = "documents"
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")
    selections = relationship("Selection", back_populates="document", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Document {self.name}>"


class DocumentVersion(Base):
    """A version of the document (v1, v2, v3...)"""
    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_document_latest", "document_id", "is_latest"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)
    ingested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_latest = Column(Boolean, default=False, nullable=False)
    ingestion_metadata = Column(JSON, nullable=True)  # OCR provider, settings used, etc.
    
    # Relationships
    document = relationship("Document", back_populates="versions")
    nodes = relationship("Node", back_populates="version", cascade="all, delete-orphan")
    generations = relationship("Generation", back_populates="version")
    
    def __repr__(self):
        return f"<DocumentVersion {self.document.name} v{self.version_number}>"


class Node(Base):
    """A single section/heading in the document"""
    __tablename__ = "nodes"
    __table_args__ = (
        Index("ix_node_parent", "parent_id"),
        Index("ix_node_version", "version_id"),
        Index("ix_node_content_hash", "content_hash"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=False)
    parent_id = Column(String(36), ForeignKey("nodes.id"), nullable=True)
    
    # Content
    heading = Column(String(255), nullable=False)
    level = Column(Integer, nullable=False)  # 1=top-level, 2=subsection, etc.
    body_text = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False)  # SHA256
    
    # Metadata
    hierarchical_path = Column(String(512), nullable=True)  # /Safety/Warnings/Pressure
    position_in_parent = Column(Integer, nullable=True)  # Order among siblings
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_modified_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # OCR metadata
    ocr_confidence = Column(Float, nullable=True)  # 0-1, how confident was OCR?
    is_image_based = Column(Boolean, default=False)  # Extracted from image, not text
    
    # Relationships
    version = relationship("DocumentVersion", back_populates="nodes")
    parent = relationship("Node", remote_side=[id], backref="children")
    mappings_from_v1 = relationship(
        "NodeMapping",
        foreign_keys="NodeMapping.v1_node_id",
        back_populates="v1_node"
    )
    mappings_to_v2 = relationship(
        "NodeMapping",
        foreign_keys="NodeMapping.v2_node_id",
        back_populates="v2_node"
    )
    in_selections = relationship(
        "SelectionNode",
        back_populates="node",
        cascade="all, delete-orphan"
    )
    
    def __repr__(self):
        return f"<Node {self.heading} (level {self.level})>"


class NodeMapping(Base):
    """Tracks how nodes changed between versions"""
    __tablename__ = "node_mappings"
    __table_args__ = (
        Index("ix_mapping_v1", "v1_node_id"),
        Index("ix_mapping_v2", "v2_node_id"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    v1_node_id = Column(String(36), ForeignKey("nodes.id"), nullable=True)
    v2_node_id = Column(String(36), ForeignKey("nodes.id"), nullable=True)
    
    change_type = Column(SQLEnum(ChangeType), nullable=False)
    similarity_score = Column(Float, default=1.0, nullable=False)  # 0-1
    diff_summary = Column(Text, nullable=True)  # Human-readable diff
    matching_strategy = Column(String(50), nullable=False)  # "path", "hash", "fuzzy", etc.
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    v1_node = relationship(
        "Node",
        foreign_keys=[v1_node_id],
        back_populates="mappings_from_v1"
    )
    v2_node = relationship(
        "Node",
        foreign_keys=[v2_node_id],
        back_populates="mappings_to_v2"
    )
    
    def __repr__(self):
        return f"<NodeMapping {self.change_type}>"


class Selection(Base):
    """User-selected nodes for test case generation (version-pinned)"""
    __tablename__ = "selections"
    __table_args__ = (
        Index("ix_selection_document", "document_id"),
        Index("ix_selection_version", "version_pinned_to"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False)
    version_pinned_to = Column(String(36), ForeignKey("document_versions.id"), nullable=False)
    
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    document = relationship("Document", back_populates="selections")
    version = relationship("DocumentVersion")
    selection_nodes = relationship(
        "SelectionNode",
        back_populates="selection",
        cascade="all, delete-orphan"
    )
    generations = relationship("Generation", back_populates="selection", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Selection {self.name}>"


class SelectionNode(Base):
    """Junction table: which nodes are in a selection"""
    __tablename__ = "selection_nodes"
    __table_args__ = (
        Index("ix_selection_node", "selection_id", "node_id"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    selection_id = Column(String(36), ForeignKey("selections.id"), nullable=False)
    node_id = Column(String(36), ForeignKey("nodes.id"), nullable=False)
    position_in_selection = Column(Integer, nullable=False)  # Order matters
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    selection = relationship("Selection", back_populates="selection_nodes")
    node = relationship("Node", back_populates="in_selections")
    
    def __repr__(self):
        return f"<SelectionNode position={self.position_in_selection}>"


class Generation(Base):
    """LLM-generated test cases from a selection"""
    __tablename__ = "generations"
    __table_args__ = (
        Index("ix_generation_selection", "selection_id"),
        Index("ix_generation_version", "document_version"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    selection_id = Column(String(36), ForeignKey("selections.id"), nullable=False)
    document_version = Column(String(36), ForeignKey("document_versions.id"), nullable=False)
    
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    llm_provider = Column(SQLEnum(LLMProvider), nullable=False)
    
    # Prompt used (for reproducibility)
    system_prompt = Column(Text, nullable=False)
    user_prompt = Column(Text, nullable=False)
    
    # LLM outputs
    raw_llm_output = Column(Text, nullable=False)
    parsed_test_cases = Column(JSON, nullable=False)  # List of test case dicts
    generation_hash = Column(String(64), nullable=False)  # SHA256 of (selection + version)
    
    # Metadata
    input_text_hash = Column(String(64), nullable=True)  # Hash of reconstructed text
    error_message = Column(Text, nullable=True)  # If parsing failed partially
    
    # Relationships
    selection = relationship("Selection", back_populates="generations")
    version = relationship("DocumentVersion", back_populates="generations")
    test_cases = relationship("TestCase", back_populates="generation", cascade="all, delete-orphan")
    staleness_checks = relationship("StalenessCheck", back_populates="generation", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Generation {len(self.test_cases)} test cases>"


class TestCase(Base):
    """Single test case in a generation"""
    __tablename__ = "test_cases"
    __table_args__ = (
        Index("ix_test_generation", "generation_id"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    generation_id = Column(String(36), ForeignKey("generations.id"), nullable=False)
    
    # Content
    test_name = Column(String(255), nullable=False)
    preconditions = Column(Text, nullable=False)
    steps = Column(JSON, nullable=False)  # List of strings
    expected_result = Column(Text, nullable=False)
    priority = Column(SQLEnum(Priority), default=Priority.MEDIUM, nullable=False)
    
    # Staleness tracking (denormalized for performance)
    staleness_status = Column(SQLEnum(StalenessLevel), default=StalenessLevel.UNKNOWN, nullable=False)
    staleness_confidence = Column(Float, default=0.0, nullable=False)  # 0-1
    staleness_summary = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_staleness_check = Column(DateTime, nullable=True)
    
    # Relationships
    generation = relationship("Generation", back_populates="test_cases")
    staleness_checks = relationship("StalenessCheck", back_populates="test_case")
    
    def __repr__(self):
        return f"<TestCase {self.test_name}>"


class StalenessCheck(Base):
    """Record of staleness analysis"""
    __tablename__ = "staleness_checks"
    __table_args__ = (
        Index("ix_staleness_generation", "generation_id"),
        Index("ix_staleness_check_time", "checked_at"),
    )
    
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    generation_id = Column(String(36), ForeignKey("generations.id"), nullable=False)
    test_case_id = Column(String(36), ForeignKey("test_cases.id"), nullable=True)  # Null for whole generation
    
    # What we checked against
    checked_against_version = Column(String(36), ForeignKey("document_versions.id"), nullable=False)
    
    # Results
    staleness_level = Column(SQLEnum(StalenessLevel), nullable=False)
    confidence_score = Column(Float, nullable=False)  # 0-1
    
    # Diff information
    original_text = Column(Text, nullable=True)  # Text at generation time
    current_text = Column(Text, nullable=True)  # Text at check time
    diff_summary = Column(Text, nullable=True)
    
    # Detection method
    detection_method = Column(String(50), nullable=False)  # "exact_hash", "fuzzy", "semantic"
    
    checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    generation = relationship("Generation", back_populates="staleness_checks")
    test_case = relationship("TestCase", back_populates="staleness_checks")
    version = relationship("DocumentVersion")
    
    def __repr__(self):
        return f"<StalenessCheck {self.staleness_level}>"
