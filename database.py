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
        # Initialize git in the same directory as the database file
        if GIT_AVAILABLE:
            import os
            db_dir = os.path.dirname(os.path.abspath(db_path)) or "."
            self.git_vc = GitVersionControl(db_dir, self)
        else:
            self.git_vc = None
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
            import os
            db_dir = os.path.dirname(os.path.abspath(new_db_path)) or "."
            self.git_vc = GitVersionControl(db_dir, self)
    
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
            import os
            db_dir = os.path.dirname(os.path.abspath(new_db_path)) or "."
            self.git_vc = GitVersionControl(db_dir, self)
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
                    SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at,
                           'created' as activity_type, n.created_at as activity_time
                    FROM notes n
                    LEFT JOIN tasks t ON n.id = t.note_id
                    WHERE date(n.created_at) = ?
                    ORDER BY n.created_at DESC
                """
                cursor = conn.execute(query, (date_str,))
            elif activity_type == 'modified':
                query = """
                    SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at,
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
                        SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at,
                               'created' as activity_type, n.created_at as activity_time
                        FROM notes n
                        LEFT JOIN tasks t ON n.id = t.note_id
                        WHERE date(n.created_at) = ?
                        
                        UNION
                        
                        SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at,
                               'modified' as activity_type, n.modified_at as activity_time
                        FROM notes n
                        LEFT JOIN tasks t ON n.id = t.note_id
                        WHERE date(n.modified_at) = ? AND date(n.created_at) != ?
                        
                        UNION
                        
                        SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at,
                               'completed' as activity_type, t.completed_at as activity_time
                        FROM notes n
                        JOIN tasks t ON n.id = t.note_id
                        WHERE date(t.completed_at) = ? AND t.completed_at IS NOT NULL
                    )
                    ORDER BY activity_time DESC
                """
                cursor = conn.execute(query, (date_str, date_str, date_str, date_str))
            
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
                    completed_at TIMESTAMP,
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
            
            # Add completed_at column to tasks table if it doesn't exist
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN completed_at TIMESTAMP")
            except sqlite3.OperationalError:
                pass  # Column already exists
            
            conn.commit()
    
    def get_children(self, parent_id: int) -> List[Dict]:
        """Get direct children of a note"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at
                FROM notes n
                LEFT JOIN tasks t ON n.id = t.note_id
                WHERE n.parent_id = ? 
                ORDER BY n.position, n.id
            """, (parent_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_next_child_position(self, parent_id: int) -> int:
        """Get the next available position for a new child of the given parent"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM notes WHERE parent_id = ?",
                (parent_id,)
            )
            return cursor.fetchone()[0]

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
    
    def update_note(self, note_id: int, content: str, force_update: bool = False):
        """Update note content

        Args:
            note_id: The ID of the note to update
            content: The new content for the note
            force_update: If True, skip the change check and always update
        """
        # Check if content actually changed (unless force_update is True)
        with sqlite3.connect(self.db_path) as conn:
            if not force_update:
                cursor = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,))
                row = cursor.fetchone()
                if row and row[0] == content:
                    # No change, don't update or commit
                    return

            # Content changed (or force_update=True), update it
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
                SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at
                FROM notes n
                LEFT JOIN tasks t ON n.id = t.note_id
                WHERE n.id = ?
            """, (note_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def toggle_task(self, note_id: int) -> str:
        """Toggle task status for a note: no task -> active -> complete -> cancelled -> no task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT status FROM tasks WHERE note_id = ?", (note_id,))
            task = cursor.fetchone()
            
            if task:
                current_status = task[0]
                if current_status == 'active':
                    # Active -> Complete (set completed_at timestamp)
                    new_status = 'complete'
                    conn.execute("UPDATE tasks SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE note_id = ?", (new_status, note_id))
                elif current_status == 'complete':
                    # Complete -> Cancelled (clear completed_at)
                    new_status = 'cancelled'
                    conn.execute("UPDATE tasks SET status = ?, completed_at = NULL WHERE note_id = ?", (new_status, note_id))
                else:  # cancelled or any other status
                    # Cancelled -> Remove task (no task)
                    conn.execute("DELETE FROM tasks WHERE note_id = ?", (note_id,))
                    new_status = None
            else:
                # No task -> Create new active task with default priority 4
                new_status = 'active'
                conn.execute("INSERT INTO tasks (note_id, status, priority) VALUES (?, ?, ?)", (note_id, new_status, 4))
            
            # Note: We intentionally don't update note's modified_at for task status changes
            # Task completions are tracked separately via completed_at and task status
            
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
                # Create task if it doesn't exist with default priority 4
                conn.execute("INSERT INTO tasks (note_id, status, priority) VALUES (?, 'active', ?)", (note_id, 4))
            
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
    
    def search_notes(self, search_term: str) -> List[Dict]:
        """Search notes by content, returns list of matching notes"""
        if not search_term.strip():
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT n.*, t.status as task_status, t.priority, t.start_date, t.due_date, t.completed_at
                FROM notes n
                LEFT JOIN tasks t ON n.id = t.note_id
                WHERE n.content LIKE ?
                ORDER BY n.modified_at DESC
                LIMIT 100
            """, (f"%{search_term}%",))
            
            return [dict(row) for row in cursor.fetchall()]


class GitVersionControl:
    """Git-based version control for notes database"""
    
    def __init__(self, repo_path: str = ".", db_manager=None):
        self.repo_path = repo_path
        self.repo = None
        self.undo_stack = []  # Stack of commit IDs we can undo to
        self.redo_stack = []  # Stack of commit IDs we can redo to
        self.db_manager = db_manager  # Reference to database manager for proper cleanup
        if GIT_AVAILABLE:
            self.init_repo()
    
    def init_repo(self):
        """Initialize git repository if it doesn't exist"""
        try:
            # Try to open existing repo
            self.repo = pygit2.Repository(self.repo_path)
            # Rebuild undo stack from existing git history
            self._rebuild_undo_stack_from_history()
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
        """Undo last change by creating a branch to preserve history"""
        if not self.repo or not self.undo_stack:
            return False
            
        try:
            # Ensure database connections are closed before git operations
            self._close_database_connections()
            
            # Get current HEAD to preserve on a branch
            current_head = self.repo.head.target
            current_head_str = str(current_head)
            
            # Get commit to undo to
            undo_to_commit_id = self.undo_stack.pop()
            undo_commit = self.repo[undo_to_commit_id]
            
            # Create a branch to preserve the current state
            import time
            branch_name = f"undone-{int(time.time())}"
            try:
                # Check if branch already exists
                if branch_name in self.repo.branches.local:
                    print(f"Branch '{branch_name}' already exists")
                else:
                    current_commit = self.repo[current_head]  # Convert Oid to Commit
                    new_branch = self.repo.branches.local.create(branch_name, current_commit)
                    print(f"Created branch '{branch_name}' to preserve undone commits")
                    print(f"Branch points to commit: {new_branch.target}")
                    
                # List all branches after creation
                print(f"All local branches after creation: {list(self.repo.branches.local)}")
                    
            except Exception as e:
                print(f"Could not create preservation branch: {e}")
                import traceback
                traceback.print_exc()
            
            # Reset to the undo commit with retry logic
            import time
            for attempt in range(3):
                try:
                    self.repo.reset(undo_commit.id, pygit2.GIT_RESET_HARD)
                    break
                except Exception as reset_error:
                    print(f"Reset attempt {attempt + 1} failed: {reset_error}")
                    if attempt < 2:  # Not the last attempt
                        print("Retrying with additional cleanup...")
                        self._close_database_connections()
                        time.sleep(0.5)
                    else:
                        raise reset_error
            
            # Add current HEAD to redo stack
            self.redo_stack.append(current_head_str)
            
            return True
            
        except Exception as e:
            print(f"Git undo failed: {e}")
            return False
    
    def redo(self) -> bool:
        """Redo by moving forward in history"""
        if not self.repo or not self.redo_stack:
            return False
            
        try:
            # Ensure database connections are closed before git reset
            self._close_database_connections()
            
            # Get current HEAD to put back on undo stack
            current_head = str(self.repo.head.target)
            
            # Get commit to redo to
            redo_to_commit_id = self.redo_stack.pop()
            redo_commit = self.repo[redo_to_commit_id]
            
            # Reset to the redo commit with retry logic
            import time
            for attempt in range(3):
                try:
                    self.repo.reset(redo_commit.id, pygit2.GIT_RESET_HARD)
                    break
                except Exception as reset_error:
                    print(f"Redo reset attempt {attempt + 1} failed: {reset_error}")
                    if attempt < 2:  # Not the last attempt
                        print("Retrying with additional cleanup...")
                        self._close_database_connections()
                        time.sleep(0.5)
                    else:
                        raise reset_error
            
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
    
    def get_commit_tree(self, limit: int = 50) -> List[Dict]:
        """Get complete commit tree structure with branching information"""
        if not self.repo:
            return []
            
        try:
            # Get all refs (branches, HEAD)
            refs = []
            try:
                refs.append(('HEAD', self.repo.head.target))
            except:
                pass
            
            # Get all branches (both local and remote)
            try:
                # Get local branches
                for branch_name in self.repo.branches.local:
                    try:
                        branch = self.repo.branches.local[branch_name]
                        refs.append((f"local/{branch_name}", branch.target))
                        print(f"Found local branch: {branch_name}")
                    except Exception as e:
                        print(f"Error accessing local branch {branch_name}: {e}")
                        continue
                
                # Get remote branches
                for branch_name in self.repo.branches.remote:
                    try:
                        branch = self.repo.branches.remote[branch_name]
                        refs.append((f"remote/{branch_name}", branch.target))
                        print(f"Found remote branch: {branch_name}")
                    except Exception as e:
                        print(f"Error accessing remote branch {branch_name}: {e}")
                        continue
                        
            except Exception as e:
                print(f"Error enumerating branches: {e}")
                # Fallback to old method
                for branch_name in self.repo.branches:
                    try:
                        branch = self.repo.branches[branch_name]
                        refs.append((branch_name, branch.target))
                        print(f"Found branch (fallback): {branch_name}")
                    except:
                        continue
            
            # Also include commits from undo/redo stacks to show "unreachable" commits
            for commit_id in self.undo_stack + self.redo_stack:
                try:
                    commit_oid = self.repo.get(commit_id)
                    if commit_oid:
                        refs.append((f'undo/redo-{commit_id[:8]}', commit_oid.id))
                except:
                    continue
            
            # Debug: Print all found refs
            print(f"Found {len(refs)} refs for commit tree:")
            for ref_name, ref_target in refs:
                print(f"  {ref_name}: {ref_target}")
            
            # Collect all commits reachable from any ref
            all_commits = {}
            commit_children = {}  # Track parent->children relationships
            
            for ref_name, ref_target in refs:
                try:
                    for commit in self.repo.walk(ref_target):
                        commit_id = str(commit.id)
                        
                        if commit_id not in all_commits:
                            all_commits[commit_id] = {
                                'id': commit_id,
                                'message': commit.message.strip(),
                                'author': commit.author.name,
                                'date': datetime.fromtimestamp(commit.commit_time),
                                'parents': [str(p) for p in commit.parent_ids],
                                'refs': [],
                                'is_head': False
                            }
                        
                        # Mark if this is the HEAD commit
                        if ref_name == 'HEAD':
                            all_commits[commit_id]['is_head'] = True
                        
                        # Add ref information
                        if ref_name not in all_commits[commit_id]['refs']:
                            all_commits[commit_id]['refs'].append(ref_name)
                        
                        # Track parent-child relationships
                        for parent_id in commit.parent_ids:
                            parent_id_str = str(parent_id)
                            if parent_id_str not in commit_children:
                                commit_children[parent_id_str] = []
                            if commit_id not in commit_children[parent_id_str]:
                                commit_children[parent_id_str].append(commit_id)
                        
                        if len(all_commits) >= limit:
                            break
                except Exception as e:
                    print(f"Error walking commits from {ref_name}: {e}")
                    continue
            
            # Convert to list and sort by date (newest first)
            commit_list = list(all_commits.values())
            commit_list.sort(key=lambda x: x['date'], reverse=True)
            
            # Add tree structure information
            for commit in commit_list:
                commit_id = commit['id']
                commit['children'] = commit_children.get(commit_id, [])
                commit['has_multiple_children'] = len(commit['children']) > 1
                commit['has_multiple_parents'] = len(commit['parents']) > 1
            
            return commit_list[:limit]
            
        except Exception as e:
            print(f"Failed to get commit tree: {e}")
            return []
    
    def _close_database_connections(self):
        """Close all database connections and remove WAL files on Windows"""
        import os
        import time
        
        # Force garbage collection to close any lingering connections
        import gc
        gc.collect()
        
        # Try to force close any open database connections
        if self.db_manager:
            # Force a checkpoint to write WAL to main database
            try:
                import sqlite3
                conn = sqlite3.connect(self.db_manager.db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
                conn.close()
                print("Forced WAL checkpoint to main database")
            except Exception as e:
                print(f"Could not force WAL checkpoint: {e}")
        
        # Force garbage collection again
        gc.collect()
        
        # On Windows, also try to remove WAL files that might lock the database
        if os.name == 'nt':  # Windows
            db_file = self.db_manager.db_path if self.db_manager else "notes.db"
            wal_file = db_file + "-wal"
            shm_file = db_file + "-shm"
            
            # Try to remove WAL files if they exist
            for file_path in [wal_file, shm_file]:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Removed SQLite WAL file: {file_path}")
                    except OSError as e:
                        print(f"Could not remove WAL file {file_path}: {e}")
            
            # Give Windows a moment to release file handles
            time.sleep(0.2)
        
        # Additional step: Try to switch to DELETE mode to avoid WAL
        if self.db_manager:
            try:
                import sqlite3
                conn = sqlite3.connect(self.db_manager.db_path)
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.close()
                print("Switched to DELETE journal mode")
            except Exception as e:
                print(f"Could not switch journal mode: {e}")
        
        # Give more time for file handles to be released
        time.sleep(0.3)
    
    def _rebuild_undo_stack_from_history(self):
        """Rebuild the undo stack from git commit history"""
        try:
            if not self.repo:
                return
            
            # Get the current HEAD commit
            try:
                current_head = self.repo.head.target
            except pygit2.GitError:
                # No HEAD (empty repo), nothing to rebuild
                return
            
            # Walk through the commit history to build undo stack
            commits = []
            for commit in self.repo.walk(current_head, pygit2.GIT_SORT_TIME):
                commits.append(str(commit.id))
                # Limit to last 50 commits to avoid excessive memory usage
                if len(commits) >= 50:
                    break
            
            # The undo stack should contain all commits except the current HEAD
            # (because HEAD is what we're currently at, so undoing goes to the previous commit)
            if len(commits) > 1:
                self.undo_stack = commits[1:]  # Skip current HEAD, keep the rest
                print(f"Rebuilt undo stack with {len(self.undo_stack)} commits")
            else:
                self.undo_stack = []
                print("No previous commits to undo to")
            
            # Clear redo stack when rebuilding from history
            self.redo_stack = []
            
        except Exception as e:
            print(f"Failed to rebuild undo stack from history: {e}")
            self.undo_stack = []
            self.redo_stack = []