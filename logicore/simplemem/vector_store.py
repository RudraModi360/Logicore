"""
LanceDB-based vector store for SimpleMem.

Provides:
- Semantic search (embedding similarity)
- Per-user table isolation
- Persistent storage

Based on SimpleMem: https://github.com/aiming-lab/SimpleMem
"""
import os
from typing import List, Optional
from dataclasses import asdict
import numpy as np

from .integration import MemoryEntry


class VectorStore:
    """
    LanceDB-based vector storage for memory entries.
    
    Each user gets their own table for isolation.
    """
    
    def __init__(
        self,
        db_path: str,
        embedding_model,
        table_name: str = "memories",
        debug: bool = False
    ):
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.table_name = table_name
        self.debug = debug
        
        # Ensure directory exists
        os.makedirs(db_path, exist_ok=True)
        
        # Initialize LanceDB
        self._db = None
        self._table = None
        self._initialize()
    
    def _initialize(self):
        """Initialize LanceDB connection and table."""
        try:
            import lancedb
            
            self._db = lancedb.connect(self.db_path)
            
            # Try to open existing table
            try:
                self._table = self._db.open_table(self.table_name)
                if self.debug:
                    print(f"[VectorStore] Opened table: {self.table_name}")
            except Exception:
                # Table doesn't exist yet, will be created on first add
                self._table = None
                if self.debug:
                    print(f"[VectorStore] Table {self.table_name} will be created on first write")
                    
        except ImportError:
            raise ImportError("lancedb is required. Install with: pip install lancedb")
        except Exception as e:
            print(f"[VectorStore] Error initializing: {e}")
            raise
    
    def _create_table(self, sample_entry: MemoryEntry, sample_vector: np.ndarray):
        """Create table with schema from sample entry."""
        import pyarrow as pa
        
        vector_dim = len(sample_vector)
        
        # Define schema
        schema = pa.schema([
            pa.field("entry_id", pa.string()),
            pa.field("lossless_restatement", pa.string()),
            pa.field("keywords", pa.list_(pa.string())),
            pa.field("timestamp", pa.string()),
            pa.field("location", pa.string()),
            pa.field("persons", pa.list_(pa.string())),
            pa.field("entities", pa.list_(pa.string())),
            pa.field("topic", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), vector_dim)),
        ])
        
        self._table = self._db.create_table(
            self.table_name,
            schema=schema,
            mode="overwrite"
        )
        
        if self.debug:
            print(f"[VectorStore] Created table: {self.table_name} (dim={vector_dim})")
    
    def add_entries(self, entries: List[MemoryEntry]):
        """Add memory entries to the store."""
        if not entries:
            return
        
        # Generate embeddings for all entries
        texts = [e.lossless_restatement for e in entries]
        vectors = self.embedding_model.encode(texts, is_query=False)
        
        # Create table if needed
        if self._table is None:
            self._create_table(entries[0], vectors[0])
        
        # Prepare records
        records = []
        for entry, vector in zip(entries, vectors):
            record = {
                "entry_id": entry.entry_id,
                "lossless_restatement": entry.lossless_restatement,
                "keywords": entry.keywords or [],
                "timestamp": entry.timestamp or "",
                "location": entry.location or "",
                "persons": entry.persons or [],
                "entities": entry.entities or [],
                "topic": entry.topic or "",
                "vector": vector.tolist(),
            }
            records.append(record)
        
        # Add to table
        self._table.add(records)
        
        if self.debug:
            print(f"[VectorStore] Added {len(records)} entries")
    
    def semantic_search(self, query: str, top_k: int = 5) -> List[MemoryEntry]:
        """
        Search by semantic similarity.
        
        Pure embedding search - no LLM calls.
        Target latency: 10-50ms
        """
        if self._table is None:
            return []
        
        try:
            # Check if table has data
            if self._table.count_rows() == 0:
                return []
            
            # Generate query embedding
            query_vector = self.embedding_model.encode_single(query, is_query=True)
            
            # Search
            results = self._table.search(query_vector.tolist()).limit(top_k).to_list()
            
            # Convert to MemoryEntry objects
            entries = []
            for row in results:
                entry = MemoryEntry(
                    entry_id=row.get("entry_id", ""),
                    lossless_restatement=row.get("lossless_restatement", ""),
                    keywords=row.get("keywords", []),
                    timestamp=row.get("timestamp"),
                    location=row.get("location"),
                    persons=row.get("persons", []),
                    entities=row.get("entities", []),
                    topic=row.get("topic"),
                )
                entries.append(entry)
            
            return entries
            
        except Exception as e:
            if self.debug:
                print(f"[VectorStore] Search error: {e}")
            return []
    
    def get_all_entries(self) -> List[MemoryEntry]:
        """Get all entries (for debugging/stats)."""
        if self._table is None:
            return []
        
        try:
            results = self._table.to_pandas()
            entries = []
            for _, row in results.iterrows():
                entry = MemoryEntry(
                    entry_id=row.get("entry_id", ""),
                    lossless_restatement=row.get("lossless_restatement", ""),
                    keywords=row.get("keywords", []),
                    timestamp=row.get("timestamp"),
                )
                entries.append(entry)
            return entries
        except Exception:
            return []
    
    def clear(self):
        """Clear all entries from the table."""
        if self._table is not None:
            try:
                self._db.drop_table(self.table_name)
                self._table = None
                if self.debug:
                    print(f"[VectorStore] Cleared table: {self.table_name}")
            except Exception as e:
                if self.debug:
                    print(f"[VectorStore] Clear error: {e}")
    
    def count_rows(self) -> int:
        """Get number of entries."""
        if self._table is None:
            return 0
        try:
            return self._table.count_rows()
        except:
            return 0
