import sqlite3
import os
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import uuid
try:
    import pygit2
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False

class DatabaseManager:
    def __init__(self, db_path: str = "notes.db"):
        self.db_path = db_path
        self.git_vc = GitVersionControl() if GIT_AVAILABLE else None
        self.init_database()
        
        # Commit initial state if git is available
        if self.git_vc:
            self.git_vc.commit_changes("Initial database state")
    
    def load_database(self, new_db_path: str):
        """Load a different database file"""
        self.db_path = new_db_path
        self.init_database()
        
        # Reinitialize git for new database location
        if GIT_AVAILABLE:
            self.git_vc = GitVersionControl()
    
    def save_database_as(self, new_db_path: str):
        """Save current database to a new file"""
        import shutil
        
        # Ensure any pending changes are committed
        if self.git_vc:
            self.git_vc.commit_changes("Save before copy")
        
        # Copy the database file
        shutil.copy2(self.db_path, new_db_path)
        
        # Switch to the new database
        old_path = self.db_path
        self.db_path = new_db_path
        
        # Reinitialize git for new location
        if GIT_AVAILABLE:
            self.git_vc = GitVersionControl()
            self.git_vc.commit_changes(f"Saved from {old_path}")
        
        return True
    
    def get_current_database_path(self) -> str:
        """Get the current database file path"""
        return self.db_path
    
    def rebuild_paths(self):
        """Rebuild all note paths to ensure consistency"""
        with sqlite3.connect(self.db_path) as conn:
            # Start with root and rebuild all paths recursively
            def rebuild_node_paths(node_id, parent_path=""):
                # Get current node
                cursor = conn.execute("SELECT id, parent_id FROM notes WHERE id = ?", (node_id,))
                node = cursor.fetchone()
                if not node:
                    return
                
                # Calculate correct path
                if node_id == 1:  # Root node
                    correct_path = "1"
                else:
                    correct_path = f"{parent_path}.{node_id}" if parent_path else str(node_id)
                
                # Update path in database
                conn.execute("UPDATE notes SET path = ? WHERE id = ?", (correct_path, node_id))
                
                # Process children
                cursor = conn.execute("SELECT id FROM notes WHERE parent_id = ? ORDER BY position", (node_id,))
                children = cursor.fetchall()
                for child in children:
                    rebuild_node_paths(child[0], correct_path)
            
            # Start rebuilding from root
            rebuild_node_paths(1)
            conn.commit()
        
        print("Path rebuilding completed")
    
    def get_notes_by_date(self, date_str: str, activity_type: str = 'all'):
        """Get notes created or modified on a specific date
        
        Args:
            date_str: Date in YYYY-MM-DD format
            activity_type: 'created', 'modified', or 'all'
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            if activity_type == 'created':
                query = """
                    SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date,
                           'created' as activity_type, n.created_at as activity_time
                    FROM notes n
                    LEFT JOIN tasks t ON n.id = t.note_id
                    WHERE date(n.created_at) = ?
                    ORDER BY n.created_at DESC
                """
                cursor = conn.execute(query, (date_str,))
            elif activity_type == 'modified':
                query = """
                    SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date,
                           'modified' as activity_type, n.modified_at as activity_time
                    FROM notes n
                    LEFT JOIN tasks t ON n.id = t.note_id
                    WHERE date(n.modified_at) = ? AND date(n.created_at) != ?
                    ORDER BY n.modified_at DESC
                """
                cursor = conn.execute(query, (date_str, date_str))
            else:  # 'all'
                query = """
                    SELECT * FROM (
                        SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date,
                               'created' as activity_type, n.created_at as activity_time
                        FROM notes n
                        LEFT JOIN tasks t ON n.id = t.note_id
                        WHERE date(n.created_at) = ?
                        
                        UNION
                        
                        SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date,
                               'modified' as activity_type, n.modified_at as activity_time
                        FROM notes n
                        LEFT JOIN tasks t ON n.id = t.note_id
                        WHERE date(n.modified_at) = ? AND date(n.created_at) != ?
                    )
                    ORDER BY activity_time DESC
                """
                cursor = conn.execute(query, (date_str, date_str, date_str))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_activity_dates(self, limit: int = 30):
        """Get dates with note activity (created or modified) for calendar/picker"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT DISTINCT date(created_at) as activity_date, 
                       COUNT(*) as note_count,
                       'created' as activity_type
                FROM notes 
                WHERE created_at IS NOT NULL
                GROUP BY date(created_at)
                
                UNION
                
                SELECT DISTINCT date(modified_at) as activity_date,
                       COUNT(*) as note_count, 
                       'modified' as activity_type
                FROM notes 
                WHERE modified_at IS NOT NULL 
                  AND date(modified_at) != date(created_at)
                GROUP BY date(modified_at)
                
                ORDER BY activity_date DESC
                LIMIT ?
            """, (limit,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def init_database(self):
        """Initialize the database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parent_id INTEGER REFERENCES notes(id),
                    content TEXT NOT NULL DEFAULT '',
                    path TEXT,
                    depth INTEGER DEFAULT 0,
                    position INTEGER DEFAULT 0,
                    is_expanded BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    note_id INTEGER PRIMARY KEY REFERENCES notes(id),
                    status TEXT CHECK(status IN ('active','complete','cancelled')) DEFAULT 'active',
                    priority INTEGER DEFAULT 0,
                    start_date DATETIME,
                    due_date DATETIME,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_parent ON notes(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_path ON notes(path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes(modified_at)")
            
            # Create root note if it doesn't exist
            cursor = conn.execute("SELECT COUNT(*) FROM notes WHERE id = 1")
            if cursor.fetchone()[0] == 0:
                conn.execute("""
                    INSERT INTO notes (id, content, path, depth, is_expanded) 
                    VALUES (1, 'Root', '1', 0, 1)
                """)
            
            # Add is_expanded column if it doesn't exist (for existing databases)
            try:
                conn.execute("ALTER TABLE notes ADD COLUMN is_expanded BOOLEAN DEFAULT 1")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            conn.commit()
    
    def get_children(self, parent_id: int) -> List[Dict]:
        """Get direct children of a note"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date
                FROM notes n
                LEFT JOIN tasks t ON n.id = t.note_id
                WHERE n.parent_id = ? 
                ORDER BY n.position, n.id
            """, (parent_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def create_note(self, parent_id: int, content: str = "", position: int = None) -> int:
        """Create a new note"""
        with sqlite3.connect(self.db_path) as conn:
            # Get parent info for path and depth
            cursor = conn.execute("SELECT path, depth FROM notes WHERE id = ?", (parent_id,))
            parent = cursor.fetchone()
            if not parent:
                raise ValueError(f"Parent note {parent_id} not found")
            
            # Handle position insertion
            if position is None:
                # Add at end
                cursor = conn.execute(
                    "SELECT COALESCE(MAX(position), -1) + 1 FROM notes WHERE parent_id = ?", 
                    (parent_id,)
                )
                position = cursor.fetchone()[0]
            else:
                # Insert at specific position - shift existing notes down
                conn.execute("""
                    UPDATE notes 
                    SET position = position + 1 
                    WHERE parent_id = ? AND position >= ?
                """, (parent_id, position))
            
            # Insert new note
            cursor = conn.execute("""
                INSERT INTO notes (parent_id, content, depth, position, path)
                VALUES (?, ?, ?, ?, ?)
            """, (parent_id, content, parent[1] + 1, position, ""))
            
            note_id = cursor.lastrowid
            
            # Update path
            new_path = f"{parent[0]}.{note_id}"
            conn.execute("UPDATE notes SET path = ? WHERE id = ?", (new_path, note_id))
            conn.commit()
            
            # Auto-commit to git
            if self.git_vc:
                self.git_vc.commit_changes(f"Create note {note_id}: {content[:50] or '(empty)'}...")
            
            return note_id
    
    def update_note(self, note_id: int, content: str):
        """Update note content"""
        # First check if content actually changed
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            if row and row[0] == content:
                # No change, don't update or commit
                return
            
            # Content changed, update it
            conn.execute("""
                UPDATE notes 
                SET content = ?, modified_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (content, note_id))
            conn.commit()
        
        # Auto-commit to git only if there was a change
        if self.git_vc:
            self.git_vc.commit_changes(f"Update note {note_id}: {content[:50]}...")
    
    def delete_note(self, note_id: int):
        """Delete note and all its children"""
        # Get note content for commit message before deletion
        note_content = ""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,))
            row = cursor.fetchone()
            if row:
                note_content = row[0]
        
        with sqlite3.connect(self.db_path) as conn:
            # Delete tasks first
            conn.execute("DELETE FROM tasks WHERE note_id IN (SELECT id FROM notes WHERE path LIKE (SELECT path || '.%' FROM notes WHERE id = ?) OR id = ?)", (note_id, note_id))
            # Delete notes
            conn.execute("DELETE FROM notes WHERE path LIKE (SELECT path || '.%' FROM notes WHERE id = ?) OR id = ?", (note_id, note_id))
            conn.commit()
        
        # Auto-commit to git
        if self.git_vc:
            self.git_vc.commit_changes(f"Delete note {note_id}: {note_content[:50]}...")
    
    def get_note(self, note_id: int) -> Optional[Dict]:
        """Get a single note by ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date
                FROM notes n
                LEFT JOIN tasks t ON n.id = t.note_id
                WHERE n.id = ?
            """, (note_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def toggle_task(self, note_id: int) -> str:
        """Toggle task status for a note: no task -> active -> complete -> no task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT status FROM tasks WHERE note_id = ?", (note_id,))
            task = cursor.fetchone()
            
            if task:
                current_status = task[0]
                if current_status == 'active':
                    # Active -> Complete
                    new_status = 'complete'
                    conn.execute("UPDATE tasks SET status = ? WHERE note_id = ?", (new_status, note_id))
                else:  # complete or any other status
                    # Complete -> Remove task (no task)
                    conn.execute("DELETE FROM tasks WHERE note_id = ?", (note_id,))
                    new_status = None
            else:
                # No task -> Create new active task
                new_status = 'active'
                conn.execute("INSERT INTO tasks (note_id, status) VALUES (?, ?)", (note_id, new_status))
            
            # Update the note's modified timestamp since metadata changed
            conn.execute("UPDATE notes SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (note_id,))
            
            conn.commit()
            
            # Auto-commit to git
            if self.git_vc:
                if new_status is None:
                    self.git_vc.commit_changes(f"Remove task status from note {note_id}")
                else:
                    self.git_vc.commit_changes(f"Toggle task {note_id} to {new_status}")
            
            return new_status
    
    def update_task_date(self, note_id: int, date_type: str, date_value: datetime):
        """Update start or due date for a task"""
        if date_type not in ['start_date', 'due_date']:
            raise ValueError("date_type must be 'start_date' or 'due_date'")
        
        with sqlite3.connect(self.db_path) as conn:
            # Ensure task exists
            cursor = conn.execute("SELECT note_id FROM tasks WHERE note_id = ?", (note_id,))
            if not cursor.fetchone():
                # Create task if it doesn't exist
                conn.execute("INSERT INTO tasks (note_id, status) VALUES (?, 'active')", (note_id,))
            
            # Update the date
            date_str = date_value.isoformat() if date_value else None
            conn.execute(f"UPDATE tasks SET {date_type} = ? WHERE note_id = ?", (date_str, note_id))
            
            # Update the note's modified timestamp since metadata changed
            conn.execute("UPDATE notes SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (note_id,))
            
            conn.commit()
        
        # Auto-commit to git
        if self.git_vc:
            date_desc = date_value.strftime('%Y-%m-%d %H:%M') if date_value else 'cleared'
            self.git_vc.commit_changes(f"Update task {note_id} {date_type} to {date_desc}")
    
    def move_note(self, note_id: int, new_parent_id: int, new_position: int):
        """Move a note to a new parent at specific position"""
        # print(f"    DB: move_note({note_id}, parent={new_parent_id}, pos={new_position})")  # Debug disabled
        with sqlite3.connect(self.db_path) as conn:
            # Get current note info
            cursor = conn.execute("SELECT parent_id, position FROM notes WHERE id = ?", (note_id,))
            current = cursor.fetchone()
            if not current:
                raise ValueError(f"Note {note_id} not found")
            
            old_parent_id, old_position = current
            
            # Get new parent info for path and depth
            cursor = conn.execute("SELECT path, depth FROM notes WHERE id = ?", (new_parent_id,))
            parent = cursor.fetchone()
            if not parent:
                raise ValueError(f"Parent note {new_parent_id} not found")
            
            # If moving within same parent, adjust positions
            if old_parent_id == new_parent_id:
                if new_position > old_position:
                    # Moving down - shift items up between old and new position
                    conn.execute("""
                        UPDATE notes 
                        SET position = position - 1 
                        WHERE parent_id = ? AND position > ? AND position <= ?
                    """, (old_parent_id, old_position, new_position))
                    new_position -= 1  # Adjust for the gap we just closed
                else:
                    # Moving up - shift items down between new and old position
                    conn.execute("""
                        UPDATE notes 
                        SET position = position + 1 
                        WHERE parent_id = ? AND position >= ? AND position < ?
                    """, (old_parent_id, new_position, old_position))
            else:
                # Moving to different parent
                # Close gap in old parent
                conn.execute("""
                    UPDATE notes 
                    SET position = position - 1 
                    WHERE parent_id = ? AND position > ?
                """, (old_parent_id, old_position))
                
                # Make room in new parent
                conn.execute("""
                    UPDATE notes 
                    SET position = position + 1 
                    WHERE parent_id = ? AND position >= ?
                """, (new_parent_id, new_position))
            
            # Update the note itself
            new_path = f"{parent[0]}.{note_id}"
            new_depth = parent[1] + 1
            
            conn.execute("""
                UPDATE notes 
                SET parent_id = ?, position = ?, path = ?, depth = ?, modified_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_parent_id, new_position, new_path, new_depth, note_id))
            
            # Update paths of all descendant notes
            cursor = conn.execute("SELECT id FROM notes WHERE path LIKE ? ORDER BY path", (f"{new_path}.%",))
            descendants = cursor.fetchall()
            
            for (desc_id,) in descendants:
                cursor = conn.execute("SELECT path, parent_id FROM notes WHERE id = ?", (desc_id,))
                desc_path, desc_parent_id = cursor.fetchone()
                
                # Get parent depth
                cursor = conn.execute("SELECT depth FROM notes WHERE id = ?", (desc_parent_id,))
                parent_depth = cursor.fetchone()[0]
                
                new_desc_depth = parent_depth + 1
                conn.execute("UPDATE notes SET depth = ? WHERE id = ?", (new_desc_depth, desc_id))
            
            conn.commit()
            
        # Auto-commit to git
        if self.git_vc:
            self.git_vc.commit_changes(f"Move note {note_id} to parent {new_parent_id}")
    
    def save_expansion_state(self, note_id: int, is_expanded: bool):
        """Save the expansion state of a note"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE notes 
                SET is_expanded = ? 
                WHERE id = ?
            """, (is_expanded, note_id))
            conn.commit()


class GitVersionControl:
    """Git-based version control for notes database"""
    
    def __init__(self, repo_path: str = "."):
        self.repo_path = repo_path
        self.repo = None
        self.undo_stack = []  # Stack of commit IDs we can undo to
        self.redo_stack = []  # Stack of commit IDs we can redo to
        if GIT_AVAILABLE:
            self.init_repo()
    
    def init_repo(self):
        """Initialize git repository if it doesn't exist"""
        try:
            # Try to open existing repo
            self.repo = pygit2.Repository(self.repo_path)
        except pygit2.GitError:
            # Create new repo
            self.repo = pygit2.init_repository(self.repo_path)
            
            # Create initial commit
            self.commit_changes("Initial commit")
    
    def commit_changes(self, message: str = "Update notes") -> bool:
        """Commit current state of notes database"""
        if not self.repo:
            return False
            
        try:
            # Add notes.db to staging
            index = self.repo.index
            index.add("notes.db")
            index.write()
            
            # Get signature
            try:
                signature = self.repo.default_signature
            except KeyError:
                # Create default signature if none exists
                signature = pygit2.Signature("Notes App", "notes@app.local")
            
            # Create commit
            tree = index.write_tree()
            
            # Get parent commits
            try:
                parent = [self.repo.head.target]
            except pygit2.GitError:
                parent = []
            
            commit_id = self.repo.create_commit(
                "HEAD",
                signature,
                signature,
                message,
                tree,
                parent
            )
            
            # Add previous HEAD to undo stack (if there was one)
            if parent:
                self.undo_stack.append(str(parent[0]))
                # Limit undo stack size
                if len(self.undo_stack) > 100:
                    self.undo_stack.pop(0)
            
            # Clear redo stack on new commit
            self.redo_stack.clear()
            
            return True
            
        except Exception as e:
            print(f"Git commit failed: {e}")
            return False
    
    def undo(self) -> bool:
        """Undo last change by reverting to previous commit"""
        if not self.repo or not self.undo_stack:
            return False
            
        try:
            # Get current HEAD to put on redo stack
            current_head = str(self.repo.head.target)
            
            # Get commit to undo to
            undo_to_commit_id = self.undo_stack.pop()
            undo_commit = self.repo[undo_to_commit_id]
            
            # Reset to the undo commit
            self.repo.reset(undo_commit.id, pygit2.GIT_RESET_HARD)
            
            # Add current HEAD to redo stack
            self.redo_stack.append(current_head)
            
            return True
            
        except Exception as e:
            print(f"Git undo failed: {e}")
            return False
    
    def redo(self) -> bool:
        """Redo by moving forward in history"""
        if not self.repo or not self.redo_stack:
            return False
            
        try:
            # Get current HEAD to put back on undo stack
            current_head = str(self.repo.head.target)
            
            # Get commit to redo to
            redo_to_commit_id = self.redo_stack.pop()
            redo_commit = self.repo[redo_to_commit_id]
            
            # Reset to the redo commit
            self.repo.reset(redo_commit.id, pygit2.GIT_RESET_HARD)
            
            # Add current HEAD back to undo stack
            self.undo_stack.append(current_head)
            
            return True
            
        except Exception as e:
            print(f"Git redo failed: {e}")
            return False
    
    def get_history(self, limit: int = 20) -> List[Dict]:
        """Get commit history"""
        if not self.repo:
            return []
            
        try:
            history = []
            for commit in self.repo.walk(self.repo.head.target):
                history.append({
                    'id': str(commit.id),
                    'message': commit.message.strip(),
                    'author': commit.author.name,
                    'date': datetime.fromtimestamp(commit.commit_time),
                })
                if len(history) >= limit:
                    break
            return history
        except Exception as e:
            print(f"Failed to get git history: {e}")
            return []