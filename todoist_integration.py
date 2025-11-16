"""
Todoist Integration Module
Syncs tasks from Todoist into the Task Notes application
"""

from datetime import datetime
from typing import Optional, List, Dict, Tuple
import logging

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
            print(f"      → sync_task_from_todoist START: '{task.content[:30]}'", flush=True)
            # Check if this task is already synced
            print(f"      → Checking for existing sync...", flush=True)
            existing_note_id = self.db_manager.get_note_by_todoist_id(task.id)
            logger.debug(f"Syncing task '{task.content}' (ID: {task.id}), existing_note_id: {existing_note_id}")
            print(f"      → Existing note: {existing_note_id}", flush=True)

            print(f"      → Converting task data...", flush=True)
            task_data = self._convert_task_to_note(task, parent_id)
            print(f"      → Task data converted", flush=True)

            if existing_note_id:
                # Update existing note
                note_id = existing_note_id
                print(f"      → Updating existing note {note_id}...", flush=True)
                logger.debug(f"Updating existing note {note_id}")
                self.db_manager.update_note(note_id, task_data['content'])
                print(f"      → Note content updated", flush=True)

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
                    if task_data['priority']:
                        self.db_manager.update_task_priority(note_id, task_data['priority'])

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
                print(f"      → Creating new note...", flush=True)
                logger.debug(f"Creating new note for task '{task.content}'")
                note_id = self.db_manager.create_note(
                    parent_id=task_data['parent_id'],
                    content=task_data['content']
                )
                print(f"      → Note {note_id} created", flush=True)

                # Convert to task
                print(f"      → Converting to task...", flush=True)
                logger.debug(f"Converting new note {note_id} to task")
                self.db_manager.toggle_task(note_id)  # Create active task
                print(f"      → Converted to task", flush=True)

                # Set task properties
                print(f"      → Setting task properties...", flush=True)
                logger.debug(f"Setting task properties for note {note_id}")
                if task_data['priority']:
                    print(f"      → Setting priority to {task_data['priority']}...", flush=True)
                    self.db_manager.update_task_priority(note_id, task_data['priority'])

                if task_data['due_date']:
                    print(f"      → Setting due date to {task_data['due_date']}...", flush=True)
                    self.db_manager.update_task_date(note_id, 'due_date', task_data['due_date'])
                print(f"      → Task properties set", flush=True)

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
                print(f"      → Creating sync mapping...", flush=True)
                logger.debug(f"Creating sync mapping for note {note_id} to Todoist ID {task_data['todoist_id']}")
                self.db_manager.create_todoist_mapping(
                    note_id=note_id,
                    todoist_id=task_data['todoist_id'],
                    todoist_project_id=task_data['todoist_project_id'],
                    todoist_parent_id=task_data['todoist_parent_id']
                )
                print(f"      → Sync mapping created", flush=True)

            # Update sync timestamp
            print(f"      → Updating sync timestamp...", flush=True)
            self.db_manager.update_todoist_sync(note_id)
            print(f"      → Sync timestamp updated", flush=True)

            logger.info(f"Synced task '{task.content}' (Todoist ID: {task.id}) to note {note_id}")
            print(f"      → sync_task_from_todoist COMPLETE", flush=True)
            return note_id

        except Exception as e:
            print(f"      → !!! EXCEPTION in sync_task_from_todoist: {e}", flush=True)
            logger.error(f"Error syncing task '{task.content}' (ID: {task.id}): {e}", exc_info=True)
            import traceback
            traceback.print_exc()
            raise

    def sync_all_tasks(self, project_id: str = None, parent_note_id: int = 1, limit: int = 100) -> Tuple[int, int]:
        """
        Sync all tasks from Todoist

        Args:
            project_id: Optional project ID to filter tasks
            parent_note_id: Parent note to add tasks under (default: root)
            limit: Maximum number of tasks to sync (default: 100)

        Returns:
            Tuple of (tasks_synced, errors)
        """
        if not self.api:
            raise RuntimeError("API not initialized. Set API token first.")

        try:
            print(f"\n=== TODOIST SYNC START ===", flush=True)
            print(f"[1] Disabling git auto-commits...", flush=True)
            # Disable git auto-commits during bulk sync to prevent hundreds of commits
            logger.info("Disabling git auto-commits for bulk sync")
            self.db_manager.disable_git_auto_commit()
            print(f"[1] ✓ Git auto-commits disabled", flush=True)

            # Get all tasks
            print(f"[2] Fetching tasks from Todoist API...", flush=True)
            logger.info(f"Fetching tasks from Todoist API (project_id={project_id})")
            if project_id:
                tasks = self.api.get_tasks(project_id=project_id)
            else:
                tasks = self.api.get_tasks()

            print(f"[2] ✓ Retrieved {len(tasks)} tasks from Todoist", flush=True)

            # Limit number of tasks
            if len(tasks) > limit:
                print(f"[3] Limiting to first {limit} tasks (out of {len(tasks)})", flush=True)
                tasks = tasks[:limit]
            else:
                print(f"[3] Syncing all {len(tasks)} tasks", flush=True)

            logger.info(f"Retrieved {len(tasks)} tasks from Todoist")
            synced = 0
            errors = 0

            print(f"[4] Starting task sync loop...", flush=True)
            for i, task in enumerate(tasks):
                try:
                    print(f"[4.{i+1}] Syncing task {i+1}/{len(tasks)}: '{task.content[:50]}...'", flush=True)
                    logger.debug(f"Syncing task {i+1}/{len(tasks)}: {task.content}")

                    self.sync_task_from_todoist(task, parent_note_id)

                    synced += 1
                    print(f"[4.{i+1}] ✓ Synced successfully", flush=True)
                except Exception as e:
                    print(f"[4.{i+1}] ✗ Error: {e}", flush=True)
                    logger.error(f"Failed to sync task {task.id}: {e}", exc_info=True)
                    errors += 1

            print(f"[5] Task loop complete. Synced={synced}, Errors={errors}", flush=True)

            # Re-enable git commits and do a single commit for all changes
            print(f"[6] Re-enabling git auto-commits...", flush=True)
            logger.info("Re-enabling git auto-commits")
            self.db_manager.enable_git_auto_commit()
            print(f"[6] ✓ Git auto-commits re-enabled", flush=True)

            # Create a single git commit for all synced tasks
            if self.db_manager.git_vc:
                commit_msg = f"Sync {synced} tasks from Todoist"
                if errors > 0:
                    commit_msg += f" ({errors} errors)"
                print(f"[7] Creating git commit: '{commit_msg}'...", flush=True)
                logger.info(f"Creating git commit: {commit_msg}")
                self.db_manager.git_vc.commit_changes(commit_msg)
                print(f"[7] ✓ Git commit created", flush=True)

            print(f"=== TODOIST SYNC COMPLETE === Synced: {synced}, Errors: {errors}\n", flush=True)
            logger.info(f"Sync complete: {synced} tasks synced, {errors} errors")
            return (synced, errors)

        except Exception as e:
            print(f"\n!!! SYNC FAILED WITH EXCEPTION !!!", flush=True)
            print(f"Error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            logger.error(f"Failed to sync tasks: {e}", exc_info=True)
            # Make sure to re-enable git commits even if there's an error
            print(f"Re-enabling git auto-commits after error...", flush=True)
            self.db_manager.enable_git_auto_commit()
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
