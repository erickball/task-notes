"""
Todoist Integration Module
Syncs tasks from Todoist into the Task Notes application
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple
import logging
import sqlite3

try:
    from todoist_api_python.api import TodoistAPI
    from todoist_api_python.models import Task
    TODOIST_AVAILABLE = True
except ImportError:
    TODOIST_AVAILABLE = False
    TodoistAPI = None
    Task = None

from database import DatabaseManager

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TodoistSync:
    """Handles synchronization between Todoist and Task Notes"""

    def __init__(self, db_manager: DatabaseManager, api_token: str = None):
        """
        Initialize Todoist sync

        Args:
            db_manager: DatabaseManager instance
            api_token: Todoist API token (optional, can be set later)
        """
        self.db_manager = db_manager
        self.api_token = api_token
        self.api = None

        if not TODOIST_AVAILABLE:
            logger.warning("todoist-api-python not installed. Run: pip install todoist-api-python")
        elif api_token:
            self._init_api()

    def _init_api(self):
        """Initialize Todoist API client"""
        if not TODOIST_AVAILABLE:
            raise ImportError("todoist-api-python not installed")

        if not self.api_token:
            raise ValueError("API token not set")

        try:
            self.api = TodoistAPI(self.api_token)
            logger.info("Todoist API initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Todoist API: {e}")
            raise

    def set_api_token(self, api_token: str):
        """Set or update the API token"""
        self.api_token = api_token
        self._init_api()

    def test_connection(self) -> bool:
        """Test if the API connection is working"""
        if not self.api:
            return False

        try:
            # Try to get tasks to verify connection
            self.api.get_tasks(limit=1)
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def _convert_priority(self, todoist_priority: int) -> int:
        """
        Convert Todoist priority (1-4) to Task Notes priority (0-10)

        Todoist: 1=normal, 2=medium, 3=high, 4=urgent
        Task Notes: 0-10 scale, 4=default for new tasks
        """
        priority_map = {
            1: 2,   # Normal -> 2
            2: 4,   # Medium -> 4
            3: 7,   # High -> 7
            4: 10   # Urgent -> 10
        }
        return priority_map.get(todoist_priority, 4)

    def _convert_task_to_note(self, task: Task, parent_id: int = 1) -> Dict:
        """
        Convert a Todoist task to note/task data

        Args:
            task: Todoist Task object
            parent_id: Parent note ID in the database

        Returns:
            Dict with note data
        """
        # Parse due date
        due_date = None
        if task.due:
            try:
                if task.due.datetime:
                    due_date = datetime.fromisoformat(task.due.datetime.replace('Z', '+00:00'))
                elif task.due.date:
                    due_date = datetime.strptime(task.due.date, '%Y-%m-%d')
            except Exception as e:
                logger.warning(f"Failed to parse due date for task {task.id}: {e}")

        return {
            'content': task.content,
            'parent_id': parent_id,
            'todoist_id': task.id,
            'todoist_project_id': task.project_id,
            'todoist_parent_id': task.parent_id,
            'priority': self._convert_priority(task.priority),
            'due_date': due_date,
            'is_completed': task.is_completed
        }

    def sync_task_from_todoist(self, task: Task, parent_id: int = 1) -> int:
        """
        Sync a single task from Todoist to the database

        Args:
            task: Todoist Task object
            parent_id: Parent note ID (default: root)

        Returns:
            note_id of created or updated note
        """
        try:
            # Check if this task is already synced
            existing_note_id = self.db_manager.get_note_by_todoist_id(task.id)
            logger.debug(f"Syncing task '{task.content}' (ID: {task.id}), existing_note_id: {existing_note_id}")

            task_data = self._convert_task_to_note(task, parent_id)

            if existing_note_id:
                # Update existing note
                note_id = existing_note_id
                logger.debug(f"Updating existing note {note_id}")
                self.db_manager.update_note(note_id, task_data['content'])

                # Get current note state
                note = self.db_manager.get_note(note_id)

                # If note is not a task yet, convert it to a task
                if note and not note.get('task_status'):
                    logger.debug(f"Converting note {note_id} to task")
                    self.db_manager.toggle_task(note_id)
                    note = self.db_manager.get_note(note_id)  # Refresh note data

                # Update task properties
                if note:
                    logger.debug(f"Updating task properties for note {note_id}")
                    with sqlite3.connect(self.db_manager.db_path) as conn:
                        if task_data['priority']:
                            conn.execute("""
                                UPDATE tasks SET priority = ? WHERE note_id = ?
                            """, (task_data['priority'], note_id))
                            conn.commit()

                    if task_data['due_date']:
                        self.db_manager.update_task_date(note_id, 'due_date', task_data['due_date'])

                    # Update completion status
                    current_status = note.get('task_status')
                    logger.debug(f"Current status: {current_status}, target completed: {task_data['is_completed']}")

                    if task_data['is_completed'] and current_status != 'complete':
                        # Need to get to complete state
                        logger.debug(f"Toggling to complete from {current_status}")
                        max_iterations = 4  # Prevent infinite loop
                        iterations = 0
                        while current_status != 'complete' and iterations < max_iterations:
                            current_status = self.db_manager.toggle_task(note_id)
                            iterations += 1
                            logger.debug(f"After toggle: {current_status}")
                        if iterations >= max_iterations:
                            logger.warning(f"Could not set task {note_id} to complete after {iterations} iterations")

                    elif not task_data['is_completed'] and current_status != 'active':
                        # Need to get to active state
                        logger.debug(f"Toggling to active from {current_status}")
                        max_iterations = 4
                        iterations = 0
                        while current_status != 'active' and iterations < max_iterations:
                            current_status = self.db_manager.toggle_task(note_id)
                            iterations += 1
                            logger.debug(f"After toggle: {current_status}")
                        if iterations >= max_iterations:
                            logger.warning(f"Could not set task {note_id} to active after {iterations} iterations")
            else:
                # Create new note
                logger.debug(f"Creating new note for task '{task.content}'")
                note_id = self.db_manager.create_note(
                    parent_id=task_data['parent_id'],
                    content=task_data['content']
                )

                # Convert to task
                logger.debug(f"Converting new note {note_id} to task")
                self.db_manager.toggle_task(note_id)  # Create active task

                # Set task properties
                logger.debug(f"Setting task properties for note {note_id}")
                with sqlite3.connect(self.db_manager.db_path) as conn:
                    if task_data['priority']:
                        conn.execute("""
                            UPDATE tasks SET priority = ? WHERE note_id = ?
                        """, (task_data['priority'], note_id))
                        conn.commit()

                if task_data['due_date']:
                    self.db_manager.update_task_date(note_id, 'due_date', task_data['due_date'])

                # Set completion status if needed
                if task_data['is_completed']:
                    logger.debug(f"Setting task {note_id} to complete")
                    current_status = 'active'
                    max_iterations = 4
                    iterations = 0
                    while current_status != 'complete' and iterations < max_iterations:
                        current_status = self.db_manager.toggle_task(note_id)
                        iterations += 1
                        logger.debug(f"After toggle: {current_status}")
                    if iterations >= max_iterations:
                        logger.warning(f"Could not set new task {note_id} to complete after {iterations} iterations")

                # Create sync mapping
                logger.debug(f"Creating sync mapping for note {note_id} to Todoist ID {task_data['todoist_id']}")
                self.db_manager.create_todoist_mapping(
                    note_id=note_id,
                    todoist_id=task_data['todoist_id'],
                    todoist_project_id=task_data['todoist_project_id'],
                    todoist_parent_id=task_data['todoist_parent_id']
                )

            # Update sync timestamp
            self.db_manager.update_todoist_sync(note_id)

            logger.info(f"Synced task '{task.content}' (Todoist ID: {task.id}) to note {note_id}")
            return note_id

        except Exception as e:
            logger.error(f"Error syncing task '{task.content}' (ID: {task.id}): {e}", exc_info=True)
            raise

    def sync_all_tasks(self, project_id: str = None, parent_note_id: int = 1) -> Tuple[int, int]:
        """
        Sync all tasks from Todoist

        Args:
            project_id: Optional project ID to filter tasks
            parent_note_id: Parent note to add tasks under (default: root)

        Returns:
            Tuple of (tasks_synced, errors)
        """
        if not self.api:
            raise RuntimeError("API not initialized. Set API token first.")

        try:
            # Get all tasks
            if project_id:
                tasks = self.api.get_tasks(project_id=project_id)
            else:
                tasks = self.api.get_tasks()

            synced = 0
            errors = 0

            for task in tasks:
                try:
                    self.sync_task_from_todoist(task, parent_note_id)
                    synced += 1
                except Exception as e:
                    logger.error(f"Failed to sync task {task.id}: {e}")
                    errors += 1

            logger.info(f"Sync complete: {synced} tasks synced, {errors} errors")
            return (synced, errors)

        except Exception as e:
            logger.error(f"Failed to sync tasks: {e}")
            raise

    def get_projects(self) -> List[Dict]:
        """Get list of Todoist projects"""
        if not self.api:
            raise RuntimeError("API not initialized. Set API token first.")

        try:
            projects = self.api.get_projects()
            return [{'id': p.id, 'name': p.name} for p in projects]
        except Exception as e:
            logger.error(f"Failed to get projects: {e}")
            raise
