"""
P90: Nanobot Memory Persistence System
=======================================
File-based persistent memory inspired by Claw's memdir system.

Architecture:
  - MEMORY.md: Index file (≤200 lines, ≤25KB) loaded into every conversation
  - Topic files: Individual .md files with frontmatter (type, description)
  - 4 memory types: user, feedback, project, reference
  - memory_manager.py: Core CRUD operations
  - memory_prompts.py: System prompt injection
  - memory_search.py: Relevance-based recall
"""
