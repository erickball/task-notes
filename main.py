import sys
import re
import sqlite3
from datetime import datetime, timedelta
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *
from database import DatabaseManager, GitVersionControl, GIT_AVAILABLE

try:
    from dateutil import parser as dateutil_parser
    from dateutil.relativedelta import relativedelta
    DATEUTIL_AVAILABLE = True
except ImportError:
    DATEUTIL_AVAILABLE = False

def parse_natural_date(text: str) -> datetime:
    """Parse natural language date/time expressions using dateutil"""
    if not text.strip():
        return None
    
    text = text.strip()
    now = datetime.now()
    
    # Preprocessing for common natural language patterns
    text_lower = text.lower()
    
    # Handle relative expressions that dateutil might not catch
    if text_lower == 'today':
        return now.replace(hour=9, minute=0, second=0, microsecond=0)  # Default to 9am today
    elif text_lower == 'now':
        return now
    elif text_lower == 'tomorrow':
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif text_lower == 'yesterday':
        return (now - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    
    # Handle "in X days/hours/minutes" patterns
    in_match = re.search(r'in\s+(\d+)\s*(day|hour|minute|week)s?', text_lower)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2)
        if unit == 'day':
            return now + timedelta(days=amount)
        elif unit == 'hour':
            return now + timedelta(hours=amount)
        elif unit == 'minute':
            return now + timedelta(minutes=amount)
        elif unit == 'week':
            return now + timedelta(weeks=amount)
    
    # Handle "tomorrow/today + time" combinations
    if 'tomorrow' in text_lower and any(time_word in text_lower for time_word in ['am', 'pm', ':']):
        # Extract the time part and apply it to tomorrow
        time_part = re.search(r'(\d{1,2}(?::\d{2})?(?:\s*[ap]m)?)', text_lower)
        if time_part:
            try:
                time_only = dateutil_parser.parse(time_part.group(1))
                base_date = now + timedelta(days=1)
                return base_date.replace(hour=time_only.hour, minute=time_only.minute, 
                                       second=0, microsecond=0)
            except:
                pass
    
    if 'today' in text_lower and any(time_word in text_lower for time_word in ['am', 'pm', ':']):
        # Extract the time part and apply it to today
        time_part = re.search(r'(\d{1,2}(?::\d{2})?(?:\s*[ap]m)?)', text_lower)
        if time_part:
            try:
                time_only = dateutil_parser.parse(time_part.group(1))
                return now.replace(hour=time_only.hour, minute=time_only.minute, 
                                 second=0, microsecond=0)
            except:
                pass
    
    # Use dateutil for everything else
    if DATEUTIL_AVAILABLE:
        try:
            # dateutil is very good at parsing natural language
            parsed = dateutil_parser.parse(text, default=now, fuzzy=True)
            
            # If the parsed date is in the past and no explicit date was given, 
            # assume they mean next occurrence
            if parsed < now and not any(word in text_lower for word in ['yesterday', 'ago', 'last']):
                # Check if it's just a time (no date components)
                if not any(word in text_lower for word in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']):
                    # It's probably just a time, so if it's in the past today, make it tomorrow
                    if parsed.date() == now.date():
                        parsed = parsed + timedelta(days=1)
            
            return parsed
        except Exception as e:
            print(f"dateutil parsing failed for '{text}': {e}")
    
    # Fallback: try to parse as ISO format
    try:
        return datetime.fromisoformat(text)
    except:
        pass
    
    return None

class EditableTreeItem(QTreeWidgetItem):
    def __init__(self, parent, note_data):
        super().__init__(parent)
        self.note_data = note_data
        self.note_id = note_data['id']
        self.update_display()
    
    def update_display(self):
        """Update the display text based on note data"""
        content = self.note_data['content']
        display_text = content if content.strip() else "(empty note)"
        
        # Add task indicator with consistent spacing and formatting
        if self.note_data.get('task_status'):
            status = self.note_data['task_status']
            if status == 'complete':
                display_text = f"☑ {display_text}"  # Using checkbox instead of checkmark
            elif status == 'active':
                display_text = f"☐ {display_text}"  # Using empty checkbox
            elif status == 'cancelled':
                display_text = f"✗ {display_text}"  # Using X mark for cancelled
        
        self.setText(0, display_text)
        
        # Apply strikethrough formatting for cancelled tasks
        if self.note_data.get('task_status') == 'cancelled':
            font = self.font(0)
            font.setStrikeOut(True)
            self.setFont(0, font)
        else:
            # Reset font formatting for non-cancelled tasks
            font = self.font(0)
            font.setStrikeOut(False)
            self.setFont(0, font)

class NoteTreeWidget(QTreeWidget):
    def __init__(self, db_manager):
        super().__init__()
        self.db = db_manager
        self.editing_item = None
        self.focused_root_id = 1  # Start focused on root (1)
        self.focus_changed_callback = None  # Callback for when focus changes
        self.max_tree_depth = 10  # Maximum depth to load at once for performance
        self.edit_widget = None
        
        self.setHeaderLabels(["Notes"])
        self.setRootIsDecorated(True)
        self.setItemsExpandable(True)
        
        # Add subtle borders around items
        self.setStyleSheet("""
            QTreeWidget::item {
                border: 1px dotted #f0f0f0;
                border-radius: 3px;
                padding: 2px;
                margin: 1px;
            }
            QTreeWidget::item:selected {
                background-color: #f0f0ff;
                border: 1px dotted #d0d0d0;
                color: black;
            }
            QTreeWidget::item:hover {
                background-color: #f8f8f8;
            }
        """)
        
        # Load root items
        self.load_tree()
        
        # Connect signals
        self.itemClicked.connect(self.on_item_clicked)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.itemExpanded.connect(self.on_item_expanded)
        self.itemCollapsed.connect(self.on_item_collapsed)
        
        
        # Enable multi-selection
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        
        # Enable drag and drop
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        
        # Enable context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Track click position for cursor placement
        self.last_click_pos = None
        
        # Track cursor column position for navigation
        self.preferred_column = 0
        self.task_prefix_length = 0  # Track task prefix for editing
        
        # Clipboard for cut/copy/paste operations
        self.clipboard_notes = []
        self.clipboard_operation = None  # 'cut' or 'copy'
        
    def load_tree(self, focus_root_id: int = None):
        """Load the tree from database, optionally focused on a subtree"""
        if focus_root_id is not None:
            self.focused_root_id = focus_root_id
        
        # Clear any editing state before reloading
        if self.edit_widget:
            self.edit_widget.hide()
            self.edit_widget.deleteLater()
            self.edit_widget = None
        self.editing_item = None
        
        self.clear()
        
        # Load the focused root note
        root_data = self.db.get_note(self.focused_root_id)
        if root_data:
            # If focusing on actual root (id=1), show it as the tree root
            if self.focused_root_id == 1:
                root_item = EditableTreeItem(self, root_data)
                self.load_children(root_item, 1, 0)
                # Restore expansion state
                is_expanded = root_data.get('is_expanded', 1)
                root_item.setExpanded(bool(is_expanded))
            else:
                # If focusing on a subtree, show its children as top-level items
                children = self.db.get_children(self.focused_root_id)
                for child_data in children:
                    child_item = EditableTreeItem(self, child_data)
                    self.load_children(child_item, child_data['id'], 0)
                    # Restore expansion state
                    is_expanded = child_data.get('is_expanded', 1)
                    child_item.setExpanded(bool(is_expanded))
        else:
            print(f"Focused root note {self.focused_root_id} not found!")
        
        # Notify parent window that focus changed
        if self.focus_changed_callback:
            self.focus_changed_callback(self.focused_root_id)
    
    def focus_on_subtree(self, note_id: int):
        """Focus the tree view on a specific subtree"""
        if note_id == self.focused_root_id:
            return  # Already focused on this subtree
        
        # Finish any current editing
        if self.editing_item:
            self.finish_editing()
        
        # Store current selection for potential restoration
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        selected_note_ids = [item.note_id for item in selected_items]
        
        # Load the new subtree
        self.load_tree(note_id)
        
        # Try to restore selection if any selected items are still visible
        self.restore_selection_by_ids(selected_note_ids)
    
    def get_focused_root(self) -> int:
        """Get the currently focused root note ID"""
        return self.focused_root_id
    
    def can_focus_up(self) -> bool:
        """Check if we can focus up to a parent level"""
        if self.focused_root_id == 1:
            return False  # Already at true root
        
        # Check if the focused root has a parent
        focused_note = self.db.get_note(self.focused_root_id)
        return focused_note and focused_note.get('parent_id') is not None
    
    def focus_up(self) -> bool:
        """Focus up to the parent of the current focused root"""
        if not self.can_focus_up():
            return False
        
        focused_note = self.db.get_note(self.focused_root_id)
        if focused_note and focused_note.get('parent_id'):
            parent_id = focused_note['parent_id']
            self.focus_on_subtree(parent_id)
            return True
        
        return False
    
    def get_focus_breadcrumbs(self) -> list:
        """Get the breadcrumb path to the currently focused root"""
        if self.focused_root_id == 1:
            return [{'id': 1, 'content': 'Root'}]
        
        breadcrumbs = []
        current_note = self.db.get_note(self.focused_root_id)
        
        # Build path from focused root back to true root
        while current_note:
            content = current_note['content'][:20] + "..." if len(current_note['content']) > 20 else current_note['content']
            if not content.strip():
                content = "(empty)"
            
            breadcrumbs.insert(0, {
                'id': current_note['id'],
                'content': content
            })
            
            # Move to parent
            if current_note.get('parent_id'):
                current_note = self.db.get_note(current_note['parent_id'])
            else:
                break
        
        return breadcrumbs
    
    def show_context_menu(self, position):
        """Show context menu for tree items"""
        item = self.itemAt(position)
        
        if not isinstance(item, EditableTreeItem):
            return
        
        menu = QMenu(self)
        
        # Focus on subtree action
        if item.note_id != self.focused_root_id:  # Don't show if already focused on this item
            focus_action = QAction(f"Focus on '{item.note_data['content'][:20]}...'" if len(item.note_data['content']) > 20 else f"Focus on '{item.note_data['content']}'", self)
            focus_action.triggered.connect(lambda: self.focus_on_subtree(item.note_id))
            menu.addAction(focus_action)
        
        # Separator
        if menu.actions():
            menu.addSeparator()
        
        # Standard actions
        new_child_action = QAction("New Child Note", self)
        new_child_action.triggered.connect(lambda: self.create_child_note(item))
        menu.addAction(new_child_action)
        
        new_sibling_action = QAction("New Sibling Note", self)
        new_sibling_action.triggered.connect(lambda: self.create_sibling_note(item))
        menu.addAction(new_sibling_action)
        
        menu.addSeparator()
        
        # Cut/Copy/Paste
        cut_action = QAction("Cut", self)
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(self.cut_notes)
        menu.addAction(cut_action)
        
        copy_action = QAction("Copy", self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.copy_notes)
        menu.addAction(copy_action)
        
        if self.clipboard_notes:
            paste_action = QAction("Paste", self)
            paste_action.setShortcut("Ctrl+V")
            paste_action.triggered.connect(self.paste_notes)
            menu.addAction(paste_action)
        
        menu.addSeparator()
        
        # Delete
        if item.note_id != 1:  # Don't allow deleting root
            delete_action = QAction("Delete", self)
            delete_action.setShortcut("Delete")
            delete_action.triggered.connect(self.delete_current_note)
            menu.addAction(delete_action)
        
        # Show the menu
        menu.exec(self.mapToGlobal(position))
    
    def create_child_note(self, parent_item):
        """Create a new child note under the specified parent"""
        if self.editing_item:
            self.finish_editing()
        
        # Create in database
        print(f"Creating child note at position 0 under parent {parent_item.note_id}")
        new_id = self.db.create_note(parent_item.note_id, "", 0)  # Insert at position 0
        print(f"Created child note with ID {new_id}")
        
        # Add to tree
        new_note_data = self.db.get_note(new_id)
        if new_note_data:
            new_item = EditableTreeItem(parent_item, new_note_data)
            parent_item.insertChild(0, new_item)
            # Expand parent to show new child
            parent_item.setExpanded(True)
            self.db.save_expansion_state(parent_item.note_id, True)
            
            # Clear selection and select only the new item
            self.clearSelection()
            self.setCurrentItem(new_item)
            new_item.setSelected(True)
            self.start_editing(new_item)
            
            # Refresh history panel to show this new note in timeline
            main_window = self.window()
            if hasattr(main_window, 'update_history_panel'):
                main_window.update_history_panel()
    
    def create_sibling_note(self, sibling_item):
        """Create a new sibling note after the specified item"""
        if self.editing_item:
            self.finish_editing()
        
        parent_item = sibling_item.parent()
        if parent_item and isinstance(parent_item, EditableTreeItem):
            parent_id = parent_item.note_id
            insert_position = parent_item.indexOfChild(sibling_item) + 1
        else:
            # Handle case where sibling is at root level in focused view
            if self.focused_root_id == 1:
                # True root - sibling is actually a child of root
                parent_id = 1
                insert_position = self.indexOfTopLevelItem(sibling_item) + 1
            else:
                # Focused subtree - sibling is child of focused root
                parent_id = self.focused_root_id
                insert_position = self.indexOfTopLevelItem(sibling_item) + 1
        
        # Create in database
        print(f"Creating sibling note at position {insert_position} under parent {parent_id}")
        new_id = self.db.create_note(parent_id, "", insert_position)
        print(f"Created sibling note with ID {new_id}")
        
        # Add to tree
        new_note_data = self.db.get_note(new_id)
        if new_note_data:
            if parent_item:
                new_item = EditableTreeItem(parent_item, new_note_data)
                parent_item.insertChild(insert_position, new_item)
            else:
                new_item = EditableTreeItem(self, new_note_data)
                self.insertTopLevelItem(insert_position, new_item)
            
            # Clear selection and select only the new item
            self.clearSelection()
            self.setCurrentItem(new_item)
            new_item.setSelected(True)
            self.start_editing(new_item)
            
            # Refresh history panel to show this new note in timeline
            main_window = self.window()
            if hasattr(main_window, 'update_history_panel'):
                main_window.update_history_panel()
            # Fallback: load children of non-existent root
            self.load_children(None, 1)
        
    def load_children(self, parent_item, parent_id, current_depth=0):
        """Load children for a given parent with depth limiting for performance"""
        # Performance optimization: limit depth to prevent loading massive trees
        if current_depth >= self.max_tree_depth:
            children_count = len(self.db.get_children(parent_id))
            if children_count > 0:
                placeholder = QTreeWidgetItem([f"... ({children_count} more levels - focus here to expand)"])
                placeholder.setDisabled(True)
                if parent_item is None:
                    self.addTopLevelItem(placeholder)
                else:
                    parent_item.addChild(placeholder)
            return
        
        children = self.db.get_children(parent_id)
        
        # Debug output for tree loading
        DEBUG_TREE_LOAD = False  # Set to False to disable
        if DEBUG_TREE_LOAD and parent_id == 6:  # Only debug the parent from the test
            print(f"Loading children for parent {parent_id}:")
            for i, child_data in enumerate(children):
                content = child_data.get('content', '')[:20]
                print(f"  [{child_data.get('position')}] Note {child_data.get('id')}: '{content}' -> Tree index {i}")
        
        for child_data in children:
            if parent_item is None:
                item = EditableTreeItem(self, child_data)
            else:
                item = EditableTreeItem(parent_item, child_data)
            
            # Restore expansion state and add children/placeholders
            is_expanded = child_data.get('is_expanded', 1)
            grandchildren = self.db.get_children(child_data['id'])
            
            if grandchildren:
                if bool(is_expanded):
                    # Load children immediately if expanded
                    self.load_children(item, child_data['id'], current_depth + 1)
                else:
                    # Add dummy child to make it expandable
                    dummy = QTreeWidgetItem(item)
                    dummy.setText(0, "Loading...")
            
            # Set expansion state after children are loaded
            item.setExpanded(bool(is_expanded))
    
    def on_item_expanded(self, item):
        """Load children when item is expanded and save expansion state"""
        if not isinstance(item, EditableTreeItem):
            return
            
        # Save expansion state
        self.db.save_expansion_state(item.note_id, True)
        
        # Check if this item has dummy children that need to be replaced
        if (item.childCount() == 1 and 
            item.child(0).text(0) == "Loading..."):
            # Remove dummy children and load real ones
            while item.childCount() > 0:
                item.removeChild(item.child(0))
            
            # Calculate current depth
            depth = 0
            parent = item.parent()
            while parent:
                depth += 1
                parent = parent.parent()
            
            self.load_children(item, item.note_id, depth)
    
    def on_item_collapsed(self, item):
        """Save collapsed state"""
        if isinstance(item, EditableTreeItem):
            self.db.save_expansion_state(item.note_id, False)
    
    def on_item_clicked(self, item, column):
        """Handle item clicks for editing"""
        if not isinstance(item, EditableTreeItem):
            return
            
        # Check for modifier keys
        modifiers = QApplication.keyboardModifiers()
        
        
        # Handle multi-selection
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+click toggles selection - finish editing first
            if self.editing_item:
                self.finish_editing()
            current_selection = item.isSelected()
            item.setSelected(not current_selection)
            if not current_selection:
                self.setCurrentItem(item)
            return
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Shift+click extends selection - finish editing first
            if self.editing_item:
                self.finish_editing()
            return  # Let Qt handle the range selection
        
        # Single click without modifiers
        selected_items = self.selectedItems()
        if len(selected_items) > 1:
            # Multiple items selected, finish editing and don't start new editing
            if self.editing_item:
                self.finish_editing()
            return
        
        # Finish any existing editing before starting new one
        if self.editing_item and self.editing_item != item:
            self.finish_editing()
        # Start editing immediately on single click
        self.start_editing(item, self.last_click_pos)
    
    def on_item_double_clicked(self, item, column):
        """Handle double-click"""
        # Double-click behavior can be customized here if needed
        pass
    
    def start_editing(self, item, click_pos=None):
        """Start editing an item"""
        if self.editing_item:
            self.finish_editing()
        
        self.editing_item = item
        rect = self.visualItemRect(item)
        
        # Create text edit widget with matching style
        self.edit_widget = QTextEdit()
        
        # Add task marker to the editing content if it's a task
        content = item.note_data['content']
        task_prefix = ""
        if item.note_data.get('task_status'):
            status = item.note_data['task_status']
            if status == 'complete':
                task_prefix = "☑ "
            elif status == 'active':
                task_prefix = "☐ "
            elif status == 'cancelled':
                task_prefix = "✗ "
        
        self.edit_widget.setPlainText(f"{task_prefix}{content}")
        self.task_prefix_length = len(task_prefix)  # Store for later when saving
        
        # Match the tree widget's font and styling
        self.edit_widget.setFont(self.font())
        self.edit_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setFrameStyle(0)  # No frame
        
        # Adjust margins and padding to match tree widget
        self.edit_widget.document().setDocumentMargin(0)  # No margin to match tree
        
        # Adjust geometry to match tree widget styling exactly
        # CSS has: border(1px) + margin(1px) + padding(2px) = 4px total offset
        adjusted_rect = QRect(rect)
        adjusted_rect.setX(rect.x() + 4)  # border + margin + padding
        adjusted_rect.setY(rect.y() + 4)  # border + margin + padding  
        adjusted_rect.setWidth(rect.width() - 8)  # Account for both sides (4px each)
        adjusted_rect.setHeight(rect.height() - 8)  # Account for both sides (4px each)
        
        self.edit_widget.setGeometry(adjusted_rect)
        self.edit_widget.setParent(self.viewport())
        self.edit_widget.show()
        self.edit_widget.setFocus()
        
        # Set cursor position based on click location (defer to avoid selection highlight)
        if click_pos is not None:
            # Use a timer to set cursor position after the widget is fully initialized
            def set_cursor():
                # Adjust click position for the geometry changes
                local_pos = click_pos - rect.topLeft()  # Use original rect
                # Account for the CSS padding/border adjustments
                local_pos.setX(local_pos.x() - 4)  # Adjust for border + padding
                local_pos.setY(local_pos.y() - 2)  # Adjust for border + padding
                cursor = self.edit_widget.cursorForPosition(local_pos)
                self.edit_widget.setTextCursor(cursor)
            
            QTimer.singleShot(10, set_cursor)
        
        # Install event filter to handle keyboard shortcuts while editing
        self.edit_widget.installEventFilter(self)
        
        # Connect signals
        self.edit_widget.textChanged.connect(self.on_text_changed)
    
    def start_editing_with_cursor_position(self, item, position='start'):
        """Start editing an item with specific cursor positioning"""
        if self.editing_item:
            self.finish_editing()
        
        self.editing_item = item
        rect = self.visualItemRect(item)
        
        # Create text edit widget with matching style
        self.edit_widget = QTextEdit()
        
        # Add task marker to the editing content if it's a task
        content = item.note_data['content']
        task_prefix = ""
        if item.note_data.get('task_status'):
            status = item.note_data['task_status']
            if status == 'complete':
                task_prefix = "☑ "
            elif status == 'active':
                task_prefix = "☐ "
            elif status == 'cancelled':
                task_prefix = "✗ "
        
        self.edit_widget.setPlainText(f"{task_prefix}{content}")
        self.task_prefix_length = len(task_prefix)  # Store for later when saving
        
        # Match the tree widget's font and styling
        self.edit_widget.setFont(self.font())
        self.edit_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setFrameStyle(0)  # No frame
        
        # Adjust margins and padding to match tree widget
        self.edit_widget.document().setDocumentMargin(0)  # No margin to match tree
        
        # Adjust geometry to match tree widget styling exactly
        # CSS has: border(1px) + margin(1px) + padding(2px) = 4px total offset
        adjusted_rect = QRect(rect)
        adjusted_rect.setX(rect.x() + 4)  # border + margin + padding
        adjusted_rect.setY(rect.y() + 4)  # border + margin + padding  
        adjusted_rect.setWidth(rect.width() - 8)  # Account for both sides (4px each)
        adjusted_rect.setHeight(rect.height() - 8)  # Account for both sides (4px each)
        
        self.edit_widget.setGeometry(adjusted_rect)
        self.edit_widget.setParent(self.viewport())
        self.edit_widget.show()
        self.edit_widget.setFocus()
        
        # Set cursor position based on navigation direction
        def set_cursor():
            cursor = self.edit_widget.textCursor()
            doc = self.edit_widget.document()
            
            if position == 'end':
                # Move to last line, try to maintain column position
                last_block = doc.lastBlock()
                cursor.setPosition(last_block.position())
                
                # Move to preferred column or end of line, whichever is shorter
                line_length = len(last_block.text())
                # Account for task prefix when calculating target column
                adjusted_preferred = max(0, self.preferred_column - self.task_prefix_length)
                target_column = min(adjusted_preferred, line_length - self.task_prefix_length) + self.task_prefix_length
                target_column = max(self.task_prefix_length, target_column)  # Don't go before task prefix
                cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.MoveAnchor, target_column)
                
            elif position == 'start':
                # Move to first line, try to maintain column position
                first_block = doc.firstBlock()
                cursor.setPosition(first_block.position())
                
                # Move to preferred column or end of line, whichever is shorter
                line_length = len(first_block.text())
                # Account for task prefix when calculating target column
                adjusted_preferred = max(0, self.preferred_column - self.task_prefix_length)
                target_column = min(adjusted_preferred, line_length - self.task_prefix_length) + self.task_prefix_length
                target_column = max(self.task_prefix_length, target_column)  # Don't go before task prefix
                cursor.movePosition(cursor.MoveOperation.Right, cursor.MoveMode.MoveAnchor, target_column)
            
            self.edit_widget.setTextCursor(cursor)
        
        # Use a timer to set cursor position after the widget is fully initialized
        QTimer.singleShot(10, set_cursor)
        
        # Install event filter to handle keyboard shortcuts while editing
        self.edit_widget.installEventFilter(self)
        
        # Connect signals
        self.edit_widget.textChanged.connect(self.on_text_changed)
    
    def start_editing_with_cursor_position_at(self, item, cursor_position):
        """Start editing an item with cursor at specific character position"""
        if self.editing_item:
            self.finish_editing()
        
        self.editing_item = item
        rect = self.visualItemRect(item)
        
        # Create text edit widget with matching style
        self.edit_widget = QTextEdit()
        
        # Add task marker to the editing content if it's a task
        content = item.note_data['content']
        task_prefix = ""
        if item.note_data.get('task_status'):
            status = item.note_data['task_status']
            if status == 'complete':
                task_prefix = "☑ "
            elif status == 'active':
                task_prefix = "☐ "
            elif status == 'cancelled':
                task_prefix = "✗ "
        
        self.edit_widget.setPlainText(f"{task_prefix}{content}")
        self.task_prefix_length = len(task_prefix)  # Store for later when saving
        
        # Match the tree widget's font and styling
        self.edit_widget.setFont(self.font())
        self.edit_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit_widget.setFrameStyle(0)  # No frame
        
        # Adjust margins and padding to match tree widget
        self.edit_widget.document().setDocumentMargin(0)  # No margin to match tree
        
        # Adjust geometry to match tree widget styling exactly
        # CSS has: border(1px) + margin(1px) + padding(2px) = 4px total offset
        adjusted_rect = QRect(rect)
        adjusted_rect.setX(rect.x() + 4)  # border + margin + padding
        adjusted_rect.setY(rect.y() + 4)  # border + margin + padding  
        adjusted_rect.setWidth(rect.width() - 8)  # Account for both sides (4px each)
        adjusted_rect.setHeight(rect.height() - 8)  # Account for both sides (4px each)
        
        self.edit_widget.setGeometry(adjusted_rect)
        self.edit_widget.setParent(self.viewport())
        self.edit_widget.show()
        self.edit_widget.setFocus()
        
        # Set cursor position at specific character
        def set_cursor():
            cursor = self.edit_widget.textCursor()
            # Adjust position to account for task prefix and bounds
            adjusted_pos = min(cursor_position, len(self.edit_widget.toPlainText()))
            cursor.setPosition(adjusted_pos)
            self.edit_widget.setTextCursor(cursor)
        
        # Use a timer to set cursor position after the widget is fully initialized
        QTimer.singleShot(10, set_cursor)
        
        # Install event filter to handle keyboard shortcuts while editing
        self.edit_widget.installEventFilter(self)
        
        # Connect signals
        self.edit_widget.textChanged.connect(self.on_text_changed)
        
    def finish_editing(self):
        """Finish editing and save changes"""
        if not self.editing_item or not self.edit_widget:
            return
        
        try:
            full_content = self.edit_widget.toPlainText()
            
            # Remove task prefix if it exists
            task_prefix_len = getattr(self, 'task_prefix_length', 0)
            if task_prefix_len > 0:
                new_content = full_content[task_prefix_len:]
            else:
                new_content = full_content
            
            # Parse and extract priority and dates from content
            parsed_content, priority, start_date, due_date = self.parse_note_content(new_content)
            
            # Save parsed content to database
            self.db.update_note(self.editing_item.note_id, parsed_content)
            
            # Update task fields if this note is a task and we found parsed values
            if self.editing_item.note_data.get('task_status') and (priority is not None or start_date or due_date):
                self.update_parsed_task_fields(self.editing_item.note_id, priority, start_date, due_date)
            
            # Update item with fresh data from database (includes updated modified_at)
            updated_note_data = self.db.get_note(self.editing_item.note_id)
            if updated_note_data:
                self.editing_item.note_data = updated_note_data
            else:
                # Fallback to just updating content if database query fails
                self.editing_item.note_data['content'] = new_content
            self.editing_item.update_display()
            
            # Refresh details panel to show updated modified_at timestamp
            main_window = self.window()
            if hasattr(main_window, 'update_details_panel'):
                main_window.update_details_panel()
            
            # Refresh history panel to show this modification in timeline
            if hasattr(main_window, 'update_history_panel'):
                main_window.update_history_panel()
            
        except Exception as e:
            print(f"Error finishing edit: {e}")
        finally:
            # Clean up
            if self.edit_widget:
                self.edit_widget.hide()
                self.edit_widget.deleteLater()
                self.edit_widget = None
            self.editing_item = None
    
    def parse_note_content(self, content):
        """Parse note content for priority and date patterns, return cleaned content and extracted values"""
        import re
        
        original_content = content
        priority = None
        start_date = None
        due_date = None
        
        # Split content into words for parsing
        words = content.split()
        if not words:
            return content, priority, start_date, due_date
        
        # 1. Check for priority pattern (p0-p5) at the end
        if words and re.match(r'^p[0-5]$', words[-1].lower()):
            priority_str = words[-1].lower()
            priority = int(priority_str[1])  # Extract number after 'p'
            words = words[:-1]  # Remove priority from words
            print(f"Parsed priority: {priority}")
        
        # Rejoin words for date parsing
        remaining_text = ' '.join(words)
        
        # 2. Check for due date pattern (due ...)
        due_match = re.search(r'\bdue\s+(.+?)(?:\s+start\s|$)', remaining_text, re.IGNORECASE)
        if due_match:
            due_text = due_match.group(1).strip()
            try:
                parsed_due = parse_natural_date(due_text)
                if parsed_due:
                    due_date = parsed_due.isoformat()
                    # Remove the "due ..." part from text
                    remaining_text = remaining_text[:due_match.start()] + remaining_text[due_match.end():]
                    remaining_text = remaining_text.strip()
                    print(f"Parsed due date: '{due_text}' -> {due_date}")
            except Exception as e:
                print(f"Failed to parse due date '{due_text}': {e}")
        
        # 3. Check for start date pattern (start ...)
        start_match = re.search(r'\bstart\s+(.+?)(?:\s+due\s|$)', remaining_text, re.IGNORECASE)
        if start_match:
            start_text = start_match.group(1).strip()
            try:
                parsed_start = parse_natural_date(start_text)
                if parsed_start:
                    start_date = parsed_start.isoformat()
                    # Remove the "start ..." part from text
                    remaining_text = remaining_text[:start_match.start()] + remaining_text[start_match.end():]
                    remaining_text = remaining_text.strip()
                    print(f"Parsed start date: '{start_text}' -> {start_date}")
            except Exception as e:
                print(f"Failed to parse start date '{start_text}': {e}")
        
        # Clean up any extra whitespace
        cleaned_content = ' '.join(remaining_text.split())
        
        return cleaned_content, priority, start_date, due_date
    
    def update_parsed_task_fields(self, note_id, priority, start_date, due_date):
        """Update task fields with parsed values from note content"""
        try:
            import sqlite3
            with sqlite3.connect(self.db.db_path) as conn:
                updates = []
                params = []
                
                if priority is not None:
                    updates.append("priority = ?")
                    params.append(priority)
                
                if start_date:
                    updates.append("start_date = ?")
                    params.append(start_date)
                
                if due_date:
                    updates.append("due_date = ?")
                    params.append(due_date)
                
                if updates:
                    query = f"UPDATE tasks SET {', '.join(updates)} WHERE note_id = ?"
                    params.append(note_id)
                    conn.execute(query, params)
                    conn.commit()
                    
                    # Auto-commit to git if available
                    if self.db.git_vc:
                        changes = []
                        if priority is not None:
                            changes.append(f"priority to {priority}")
                        if start_date:
                            changes.append(f"start date to {start_date}")
                        if due_date:
                            changes.append(f"due date to {due_date}")
                        self.db.git_vc.commit_changes(f"Update task {note_id}: {', '.join(changes)}")
                    
                    print(f"Updated task {note_id} with parsed values")
                    
        except Exception as e:
            print(f"Error updating task fields: {e}")
    
    def on_text_changed(self):
        """Handle text changes to resize edit widget"""
        if not self.edit_widget:
            return
        
        # Auto-resize height based on content
        doc = self.edit_widget.document()
        height = int(doc.size().height()) + 10
        rect = self.edit_widget.geometry()
        rect.setHeight(max(height, 25))
        self.edit_widget.setGeometry(rect)
    
    def keyPressEvent(self, event):
        """Handle keyboard shortcuts"""
        key = event.key()
        modifiers = event.modifiers()
        
        # Handle Tab/Shift+Tab
        if key == Qt.Key.Key_Tab:
            self.change_indentation(1)
            return
            
        elif key == Qt.Key.Key_Backtab:  # Shift+Tab
            self.change_indentation(-1)
            return
        
        # Return/Enter handling is now done in eventFilter for editing mode
        
        # Note: Other editing mode shortcuts are handled in eventFilter
        if not self.editing_item:
            # Handle navigation mode
            if key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                current = self.currentItem()
                if current:
                    self.start_editing(current)
                return
            elif key == Qt.Key.Key_Delete:
                self.delete_current_note()
                return
            elif key == Qt.Key.Key_Space and modifiers & Qt.KeyboardModifier.ControlModifier:
                self.toggle_task()
                return
            elif key == Qt.Key.Key_Up:
                self.move_to_previous_item()
                return
            elif key == Qt.Key.Key_Down:
                self.move_to_next_item()
                return
        
        super().keyPressEvent(event)
    
    def move_to_previous_item(self):
        """Move selection to previous item"""
        current = self.currentItem()
        if not current:
            return
        
        # Find previous item in tree order
        prev = self.itemAbove(current)
        if prev and isinstance(prev, EditableTreeItem):
            self.setCurrentItem(prev)
            self.start_editing_with_cursor_position(prev, 'end')
        elif isinstance(current, EditableTreeItem):
            # No previous item, start editing current item
            self.start_editing_with_cursor_position(current, 'start')
    
    def move_to_next_item(self):
        """Move selection to next item"""
        current = self.currentItem()
        if not current:
            return
        
        # Find next item in tree order
        next_item = self.itemBelow(current)
        if next_item and isinstance(next_item, EditableTreeItem):
            self.setCurrentItem(next_item)
            self.start_editing_with_cursor_position(next_item, 'start')
        elif isinstance(current, EditableTreeItem):
            # No next item, start editing current item
            self.start_editing_with_cursor_position(current, 'end')
    
    def create_new_note(self):
        """Create a new sibling note after current selection"""
        # Finish any current editing first to avoid stale references
        if self.editing_item:
            self.finish_editing()
        
        current = self.currentItem()
        
        if isinstance(current, EditableTreeItem):
            # Create as sibling after current item
            parent_item = current.parent()
            if parent_item and isinstance(parent_item, EditableTreeItem):
                parent_id = parent_item.note_id
                # Get current item's position to insert after it
                current_position = parent_item.indexOfChild(current)
                insert_position = current_position + 1
            else:
                # Current item is at root level (current is the root item itself)
                # We need to create a sibling of root, which means another child of root's parent
                # But since root has no parent, we create a child of root instead
                if current.note_id == 1:  # This is the root
                    parent_id = 1  # Create as child of root
                    parent_item = current
                    insert_position = 0
                else:
                    # This shouldn't happen with our current structure, but handle it
                    parent_id = 1
                    parent_item = None
                    current_position = self.indexOfTopLevelItem(current) 
                    insert_position = current_position + 1
        else:
            # No current item, create at root
            parent_id = 1
            parent_item = None
            insert_position = 0
        
        # Create in database with specific position
        new_id = self.db.create_note(parent_id, "", insert_position)
        
        # Add the new note directly to avoid full reload
        new_note_data = self.db.get_note(new_id)
        if new_note_data:
            if parent_item:
                # Insert as child at specific position
                new_item = EditableTreeItem(None, new_note_data)
                parent_item.insertChild(insert_position, new_item)
                # Expand parent to show new child
                parent_item.setExpanded(True)
                # Save expansion state since setExpanded doesn't trigger the event
                self.db.save_expansion_state(parent_item.note_id, True)
            else:
                # Insert at root level
                new_item = EditableTreeItem(None, new_note_data)
                self.insertTopLevelItem(insert_position, new_item)
            
            # Clear selection and select only the new item
            self.clearSelection()
            self.setCurrentItem(new_item)
            new_item.setSelected(True)
            self.start_editing(new_item)  # Start editing the new note
            
            # Refresh history panel to show this new note in timeline
            main_window = self.window()
            if hasattr(main_window, 'update_history_panel'):
                main_window.update_history_panel()
    
    def expand_item_by_id(self, note_id: int):
        """Find and expand an item by its note ID"""
        def find_and_expand(item):
            if isinstance(item, EditableTreeItem) and item.note_id == note_id:
                item.setExpanded(True)
                # Save expansion state since setExpanded doesn't trigger the event
                self.db.save_expansion_state(item.note_id, True)
                return True
            
            for i in range(item.childCount()):
                if find_and_expand(item.child(i)):
                    return True
            return False
        
        # Search from root
        for i in range(self.topLevelItemCount()):
            find_and_expand(self.topLevelItem(i))
    
    def delete_current_note(self):
        """Delete the currently selected note(s)"""
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            return
        
        # Don't delete root
        selected_items = [item for item in selected_items if item.note_id != 1]
        if not selected_items:
            return
        
        # Check if confirmation is needed
        needs_confirmation = False
        
        if len(selected_items) > 1:
            needs_confirmation = True
        else:
            # Single item - check if it has children
            item = selected_items[0]
            children = self.db.get_children(item.note_id)
            if children:
                needs_confirmation = True
        
        # Confirmation dialog if needed
        if needs_confirmation:
            if len(selected_items) == 1:
                message = f"Delete this note and all its children?"
            else:
                message = f"Delete {len(selected_items)} notes and all their children?"
            
            reply = QMessageBox.question(
                self, 
                "Delete Notes", 
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        # Finish editing if we're deleting the item being edited
        if self.editing_item in selected_items:
            self.finish_editing()
            
            # Remember parent for selection after deletion
            parent_to_select = None
            if len(selected_items) == 1:
                parent_to_select = selected_items[0].parent()
            
            # Delete from database and tree
            for item in selected_items:
                # Delete from database
                self.db.delete_note(item.note_id)
                
                # Remove from tree directly
                parent_item = item.parent()
                if parent_item:
                    parent_item.removeChild(item)
                else:
                    # This was a top-level item
                    root = self.invisibleRootItem()
                    root.removeChild(item)
            
            # Select appropriate item after deletion
            if parent_to_select:
                self.setCurrentItem(parent_to_select)
            elif self.topLevelItemCount() > 0:
                self.setCurrentItem(self.topLevelItem(0))
    
    def change_indentation(self, direction):
        """Change indentation level of selected item(s)"""
        # Get all selected items first
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            current = self.currentItem()
            if isinstance(current, EditableTreeItem):
                selected_items = [current]
            else:
                return
        
        # Check if multiple items are contiguous (have same parent and consecutive positions)
        if len(selected_items) > 1:
            if not self.are_items_contiguous(selected_items):
                return  # Only work with contiguous selections
        
        # Save selection before any operations
        selected_note_ids = [item.note_id for item in selected_items]
        
        # Always finish editing first - no restart for multi-select
        if self.editing_item:
            # Save editing state only for single selections
            was_editing = len(selected_items) == 1 and self.editing_item == selected_items[0]
            cursor_pos = 0
            if was_editing and self.edit_widget:
                cursor_pos = self.edit_widget.textCursor().position()
            
            self.finish_editing()
        else:
            was_editing = False
            cursor_pos = 0
        
        
        # Perform indentation operations on database only
        try:
            if len(selected_items) > 1:
                # Don't restart editing for multiple selections
                was_editing = False
                
                # For multiple contiguous items, we need special handling
                # All items should move to the same new parent, maintaining their relative order
                if direction > 0:  # Indenting
                    # Find the target parent (previous sibling of the first selected item)
                    first_item = self.sort_items_by_tree_position(selected_items)[0]
                    target_parent = self.get_indent_target_parent(first_item)
                    
                    if target_parent:
                        # Move all items to the target parent, maintaining order
                        sorted_items = self.sort_items_by_tree_position(selected_items)
                        for i, item in enumerate(sorted_items):
                            self.db.move_note(item.note_id, target_parent.note_id, i)
                else:  # Outdenting
                    # Process from last to first for outdenting to preserve order
                    sorted_items = self.sort_items_by_tree_position(selected_items, reverse=True)
                    for item in sorted_items:
                        self.outdent_note_db_only(item)
            else:
                # Single item
                item = selected_items[0]
                if direction > 0:
                    target_parent = self.get_indent_target_parent(item)
                    if target_parent:
                        self.db.move_note(item.note_id, target_parent.note_id, 0)
                else:
                    self.outdent_note_db_only(item)
            
            # Reload tree to reflect changes
            self.load_tree()
            
            # Restore selection (find items by their note IDs)
            self.restore_selection_by_ids(selected_note_ids)
            
            # Only restart editing for single item selections
            if was_editing and len(selected_items) == 1:
                # Find the moved item and restart editing
                def find_item_by_id(note_id):
                    def search_item(item):
                        if isinstance(item, EditableTreeItem) and item.note_id == note_id:
                            return item
                        for i in range(item.childCount()):
                            result = search_item(item.child(i))
                            if result:
                                return result
                        return None
                    
                    # Search through all items
                    for i in range(self.topLevelItemCount()):
                        result = search_item(self.topLevelItem(i))
                        if result:
                            return result
                    return None
                
                moved_item = find_item_by_id(selected_items[0].note_id)
                if moved_item:
                    self.start_editing_with_cursor_position_at(moved_item, cursor_pos)
        
        except Exception as e:
            # Restore original tree state
            self.load_tree()
            self.restore_selection_by_ids(selected_note_ids)
    
    def restore_selection_by_ids(self, note_ids):
        """Restore selection of items by their note IDs"""
        self.clearSelection()
        selected_items = []
        
        def find_and_select(item):
            if isinstance(item, EditableTreeItem) and item.note_id in note_ids:
                item.setSelected(True)
                selected_items.append(item)
                return True
            
            for i in range(item.childCount()):
                find_and_select(item.child(i))
            return False
        
        # Search through all items
        for i in range(self.topLevelItemCount()):
            find_and_select(self.topLevelItem(i))
        
        # Set the first selected item as current item for proper navigation
        if selected_items:
            self.setCurrentItem(selected_items[0])
    
    def get_indent_target_parent(self, item):
        """Get the target parent for indenting an item (previous sibling)"""
        parent_item = item.parent()
        if parent_item:
            # Find previous sibling
            current_index = parent_item.indexOfChild(item)
            if current_index > 0:
                previous_sibling = parent_item.child(current_index - 1)
                if isinstance(previous_sibling, EditableTreeItem):
                    return previous_sibling
        else:
            # Item is at root level, find previous top-level item
            current_index = self.indexOfTopLevelItem(item)
            if current_index > 0:
                previous_sibling = self.topLevelItem(current_index - 1)
                if isinstance(previous_sibling, EditableTreeItem):
                    return previous_sibling
        return None
    
    def sort_items_by_tree_position(self, items, reverse=False):
        """Sort items by their position in the tree"""
        def get_sort_key(item):
            parent = item.parent()
            if parent:
                return parent.indexOfChild(item)
            else:
                return self.indexOfTopLevelItem(item)
        
        return sorted(items, key=get_sort_key, reverse=reverse)
    
    def get_item_position(self, item):
        """Get the position of an item for sorting purposes"""
        parent = item.parent()
        if parent:
            return parent.indexOfChild(item)
        else:
            return self.indexOfTopLevelItem(item)
    
    def are_items_contiguous(self, items):
        """Check if selected items are contiguous siblings"""
        if len(items) <= 1:
            return True
        
        # Check if all items have the same parent
        parent = items[0].parent()
        if not all(item.parent() == parent for item in items):
            return False
        
        # Get positions of all items
        if parent:
            positions = [(parent.indexOfChild(item), item) for item in items]
        else:
            positions = [(self.indexOfTopLevelItem(item), item) for item in items]
        
        positions.sort()  # Sort by position
        
        # Check if positions are consecutive
        for i in range(1, len(positions)):
            if positions[i][0] != positions[i-1][0] + 1:
                return False
        
        return True
    
    def indent_note(self, item):
        """Move note to be child of previous sibling"""
        if not isinstance(item, EditableTreeItem):
            return
        
        parent_item = item.parent()
        if parent_item:
            # Find previous sibling
            current_index = parent_item.indexOfChild(item)
            if current_index > 0:
                previous_sibling = parent_item.child(current_index - 1)
                if isinstance(previous_sibling, EditableTreeItem):
                    # Move item to be child of previous sibling
                    self.move_note_to_parent(item, previous_sibling, 0)
        else:
            # Item is at root level, find previous top-level item
            current_index = self.indexOfTopLevelItem(item)
            if current_index > 0:
                previous_sibling = self.topLevelItem(current_index - 1)
                if isinstance(previous_sibling, EditableTreeItem):
                    self.move_note_to_parent(item, previous_sibling, 0)
    
    def outdent_note(self, item):
        """Move note to parent's level (become sibling of parent)"""
        if not isinstance(item, EditableTreeItem):
            return
        
        parent_item = item.parent()
        if parent_item and isinstance(parent_item, EditableTreeItem):
            grandparent_item = parent_item.parent()
            if grandparent_item and isinstance(grandparent_item, EditableTreeItem):
                # Move to grandparent level, after parent
                parent_index = grandparent_item.indexOfChild(parent_item)
                self.move_note_to_parent(item, grandparent_item, parent_index + 1)
            elif parent_item.note_id != 1:  # Don't outdent if parent is root
                # Move to root level, after parent
                parent_index = self.indexOfTopLevelItem(parent_item)
                if parent_index >= 0:
                    # This shouldn't happen with our current structure, but handle it
                    pass
    
    def outdent_note_db_only(self, item):
        """Move note to parent's level using database only (for tree reload approach)"""
        if not isinstance(item, EditableTreeItem):
            return
        
        # Get the current note data from database
        note_data = self.db.get_note(item.note_id)
        if not note_data:
            return
        
        current_parent_id = note_data['parent_id']
        if current_parent_id == 1:  # Already at root level
            return
        
        # Get parent data
        parent_data = self.db.get_note(current_parent_id)
        if not parent_data:
            return
        
        grandparent_id = parent_data['parent_id']
        if grandparent_id is None:  # Parent is root, can't outdent further
            return
        
        # Find position to insert after parent
        grandparent_children = self.db.get_children(grandparent_id)
        parent_position = -1
        for i, child in enumerate(grandparent_children):
            if child['id'] == current_parent_id:
                parent_position = i
                break
        
        if parent_position >= 0:
            # Move to grandparent, after parent
            self.db.move_note(item.note_id, grandparent_id, parent_position + 1)
    
    def move_note_to_parent(self, item, new_parent_item, position):
        """Move a note to a new parent at specific position"""
        if not isinstance(item, EditableTreeItem) or not isinstance(new_parent_item, EditableTreeItem):
            return
        
        # Finish editing if we're moving the item being edited
        if self.editing_item == item:
            self.finish_editing()
        
        # Store current location info for potential rollback
        old_parent = item.parent()
        old_position = -1
        if old_parent:
            old_position = old_parent.indexOfChild(item)
        else:
            old_position = self.indexOfTopLevelItem(item)
        
        try:
            # Update database FIRST before modifying UI
            self.db.move_note(item.note_id, new_parent_item.note_id, position)
            
            # Remove from current location after successful database update
            if old_parent:
                old_parent.removeChild(item)
            else:
                root = self.invisibleRootItem()
                root.removeChild(item)
            
            # Add to new location
            new_parent_item.insertChild(position, item)
            new_parent_item.setExpanded(True)  # Expand to show moved item
            
            # Save the expansion state to database since setExpanded doesn't trigger the event
            self.db.save_expansion_state(new_parent_item.note_id, True)
            
            # Update the item's data to reflect new parent/depth
            updated_data = self.db.get_note(item.note_id)
            if updated_data:
                item.note_data = updated_data
            
            # Keep item selected
            self.setCurrentItem(item)
            
        except Exception as e:
            # Rollback: put item back in original location if it was removed
            if old_parent and old_position >= 0:
                old_parent.insertChild(old_position, item)
            elif old_position >= 0:
                self.insertTopLevelItem(old_position, item)
            raise e
    
    def get_root_item(self):
        """Find and return the root item (note_id = 1)"""
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            if isinstance(item, EditableTreeItem) and item.note_id == 1:
                return item
        return None
    
    def delayed_refresh_after_drag(self, moved_note_ids):
        """Perform tree refresh after a delay to let PyQt finish drag processing"""
        # Store current expansion states before reload
        expansion_states = {}
        def store_expansion_state(item):
            if isinstance(item, EditableTreeItem):
                expansion_states[item.note_id] = item.isExpanded()
            for i in range(item.childCount()):
                store_expansion_state(item.child(i))
        
        for i in range(self.topLevelItemCount()):
            store_expansion_state(self.topLevelItem(i))
        
        # Do full tree reload
        self.load_tree()
        
        # Restore expansion states after reload
        def restore_expansion_state(item):
            if isinstance(item, EditableTreeItem) and item.note_id in expansion_states:
                item.setExpanded(expansion_states[item.note_id])
            for i in range(item.childCount()):
                restore_expansion_state(item.child(i))
        
        for i in range(self.topLevelItemCount()):
            restore_expansion_state(self.topLevelItem(i))
        
        # Restore selection of moved items
        self.restore_selection_by_ids(moved_note_ids)
    
    def refresh_parent(self, parent_id):
        """Refresh the children of a specific parent item without full tree reload"""
        # Find the parent item in the tree
        parent_item = self.find_item_by_id(parent_id)
        if not parent_item:
            return
        
        # Store expansion state of children before refresh
        child_expansion_states = {}
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            if isinstance(child, EditableTreeItem):
                child_expansion_states[child.note_id] = child.isExpanded()
        
        # Remove all children
        while parent_item.childCount() > 0:
            parent_item.removeChild(parent_item.child(0))
        
        # Reload children from database in correct order
        self.load_children(parent_item, parent_id)
        
        # Restore expansion states
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            if isinstance(child, EditableTreeItem) and child.note_id in child_expansion_states:
                child.setExpanded(child_expansion_states[child.note_id])
        
        # Force visual updates to ensure proper rendering
        self.viewport().update()  # Force viewport repaint
        self.update()  # Force widget update
        
        # Also update the tree widget's internal model
        self.model().layoutChanged.emit()
    
    def find_item_by_id(self, note_id):
        """Find a tree item by its note ID"""
        def search_item(item):
            if isinstance(item, EditableTreeItem) and item.note_id == note_id:
                return item
            for i in range(item.childCount()):
                result = search_item(item.child(i))
                if result:
                    return result
            return None
        
        # Search through all top-level items
        for i in range(self.topLevelItemCount()):
            result = search_item(self.topLevelItem(i))
            if result:
                return result
        return None
    
    def eventFilter(self, obj, event):
        """Filter events for the text edit widget to handle shortcuts while editing"""
        if obj == self.edit_widget and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            
            # Handle Ctrl+Z and Ctrl+Y while editing
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_Z:
                    # Get parent window to call undo
                    main_window = self.window()
                    if hasattr(main_window, 'undo'):
                        main_window.undo()
                    return True
                elif key == Qt.Key.Key_Y:
                    # Get parent window to call redo
                    main_window = self.window()
                    if hasattr(main_window, 'redo'):
                        main_window.redo()
                    return True
            
            # Handle Tab/Shift-Tab while editing
            if key == Qt.Key.Key_Tab:
                self.change_indentation(1)
                return True
            elif key == Qt.Key.Key_Backtab:
                self.change_indentation(-1)
                return True
            
            # Handle Ctrl+Space for task toggling
            elif key == Qt.Key.Key_Space and modifiers & Qt.KeyboardModifier.ControlModifier:
                self.toggle_task()
                return True
            
            # Handle Up arrow in first line
            elif key == Qt.Key.Key_Up:
                cursor = self.edit_widget.textCursor()
                # If cursor is in the first line (block 0)
                if cursor.blockNumber() == 0:
                    # Save current column position (including task prefix)
                    self.preferred_column = cursor.positionInBlock()
                    self.finish_editing()
                    self.move_to_previous_item()
                    return True
                    
            # Handle Down arrow in last line
            elif key == Qt.Key.Key_Down:
                cursor = self.edit_widget.textCursor()
                doc = self.edit_widget.document()
                last_block = doc.lastBlock()
                # If cursor is in the last line
                if cursor.blockNumber() == last_block.blockNumber():
                    # Save current column position (including task prefix)
                    self.preferred_column = cursor.positionInBlock()
                    self.finish_editing()
                    self.move_to_next_item()
                    return True
                    
            # Handle Backspace on empty note
            elif key == Qt.Key.Key_Backspace and not self.edit_widget.toPlainText():
                self.delete_empty_note_and_select_previous()
                return True
                
            # Handle Return/Enter keys for note creation and newlines
            elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    # Ctrl+Return: Create child note
                    current_item = self.editing_item
                    self.finish_editing()
                    if current_item:
                        self.create_child_note(current_item)
                    return True
                elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                    # Shift+Return: Insert newline in current note
                    cursor = self.edit_widget.textCursor()
                    cursor.insertText("\n")
                    return True
                else:
                    # Return: Create sibling note
                    current_item = self.editing_item
                    self.finish_editing()
                    if current_item:
                        self.create_sibling_note(current_item)
                    return True
                
            # Handle Escape
            elif key == Qt.Key.Key_Escape:
                self.finish_editing()
                return True
        
        # Let other events pass through
        return super().eventFilter(obj, event)
    
    def mousePressEvent(self, event):
        """Handle mouse press events for better multi-selection and cursor positioning"""
        item = self.itemAt(event.pos())
        
        if isinstance(item, EditableTreeItem):
            modifiers = event.modifiers()
            
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+click - toggle selection without clearing others
                item.setSelected(not item.isSelected())
                self.setCurrentItem(item)
                return
            elif modifiers & Qt.KeyboardModifier.ShiftModifier:
                # Shift+click - extend selection (let Qt handle this)
                super().mousePressEvent(event)
                return
            else:
                # Normal click - store position for cursor placement
                self.last_click_pos = event.pos()
        
        # Normal click - let the regular handler process it
        super().mousePressEvent(event)
    
    def dropEvent(self, event):
        """Handle drag and drop operations with delayed refresh"""
        # Get the drop target and selected items
        drop_item = self.itemAt(event.position().toPoint())
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            event.ignore()
            return
        
        # Don't allow dropping root or dropping items onto themselves
        if any(item.note_id == 1 for item in selected_items):
            event.ignore()
            return
        
        if drop_item and drop_item in selected_items:
            event.ignore()
            return
        
        # Intelligent drop logic based on drop position
        drop_indicator = self.dropIndicatorPosition()
        target_parent_id = None
        target_position = 0
        
        if drop_item and isinstance(drop_item, EditableTreeItem):
            if drop_indicator == QAbstractItemView.DropIndicatorPosition.OnItem:
                # Dropped ON the item - make it a child
                target_parent_id = drop_item.note_id
                target_position = len(self.db.get_children(drop_item.note_id))  # Add at end
            
            elif drop_indicator in [QAbstractItemView.DropIndicatorPosition.AboveItem, 
                                   QAbstractItemView.DropIndicatorPosition.BelowItem]:
                # Dropped ABOVE or BELOW the item - make it a sibling
                parent_item = drop_item.parent()
                if parent_item and isinstance(parent_item, EditableTreeItem):
                    # Has a parent in the tree
                    target_parent_id = parent_item.note_id
                    current_pos = parent_item.indexOfChild(drop_item)
                    if drop_indicator == QAbstractItemView.DropIndicatorPosition.AboveItem:
                        target_position = current_pos  # Insert before
                    else:
                        target_position = current_pos + 1  # Insert after
                else:
                    # Top level item - parent is the focused root
                    target_parent_id = self.focused_root_id
                    current_pos = self.indexOfTopLevelItem(drop_item)
                    if drop_indicator == QAbstractItemView.DropIndicatorPosition.AboveItem:
                        target_position = current_pos  # Insert before
                    else:
                        target_position = current_pos + 1  # Insert after
            else:
                # Fallback - treat as child
                target_parent_id = drop_item.note_id
                target_position = len(self.db.get_children(drop_item.note_id))
        else:
            # Drop on empty space - add to focused root
            target_parent_id = self.focused_root_id
            target_position = len(self.db.get_children(self.focused_root_id))
        
        if target_parent_id is None:
            event.ignore()
            return
        
        # Finish any editing
        if self.editing_item:
            self.finish_editing()
        
        # Perform database updates
        try:
            moved_note_ids = [item.note_id for item in selected_items]
            
            for i, item in enumerate(selected_items):
                final_position = target_position + i
                print(f"Moving note {item.note_id} to parent {target_parent_id}, position {final_position}")
                self.db.move_note(item.note_id, target_parent_id, final_position)
            
            # Accept the event and use delayed refresh
            event.accept()
            
            # Use a timer to delay the refresh until PyQt finishes drag processing
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self.delayed_refresh_after_drag(moved_note_ids))
            
        except Exception as e:
            event.ignore()
    
    def toggle_task(self):
        """Toggle task status for selected note(s)"""
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            current = self.currentItem()
            if isinstance(current, EditableTreeItem):
                selected_items = [current]
            else:
                return
        
        for item in selected_items:
            # Toggle in database
            new_status = self.db.toggle_task(item.note_id)
            
            # Update item data and display
            item.note_data['task_status'] = new_status
            item.update_display()
            
            # If this item is currently being edited, update the edit widget
            if self.editing_item == item and self.edit_widget:
                current_text = self.edit_widget.toPlainText()
                # Remove old task prefix if it exists
                if hasattr(self, 'task_prefix_length') and self.task_prefix_length > 0:
                    current_text = current_text[self.task_prefix_length:]
                
                # Add new task prefix
                task_prefix = ""
                if new_status == 'complete':
                    task_prefix = "☑ "
                elif new_status == 'active':
                    task_prefix = "☐ "
                elif new_status == 'cancelled':
                    task_prefix = "✗ "
                # If new_status is None, task_prefix remains empty (no task)
                
                # Update the edit widget
                cursor_pos = self.edit_widget.textCursor().position()
                # Adjust cursor position for prefix change
                if hasattr(self, 'task_prefix_length'):
                    cursor_pos = max(len(task_prefix), cursor_pos - self.task_prefix_length + len(task_prefix))
                
                self.edit_widget.setPlainText(f"{task_prefix}{current_text}")
                self.task_prefix_length = len(task_prefix)
                
                # Restore cursor position
                cursor = self.edit_widget.textCursor()
                cursor.setPosition(min(cursor_pos, len(self.edit_widget.toPlainText())))
                self.edit_widget.setTextCursor(cursor)
        
        # Update task dashboard
        main_window = self.window()
        if hasattr(main_window, 'update_task_dashboard'):
            main_window.update_task_dashboard()
        
        # Refresh history panel to show task toggle activity
        if hasattr(main_window, 'update_history_panel'):
            main_window.update_history_panel()
    
    def cut_notes(self):
        """Cut selected notes to clipboard"""
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            return
        
        # Don't allow cutting root
        selected_items = [item for item in selected_items if item.note_id != 1]
        if not selected_items:
            return
        
        # Store note data for clipboard
        self.clipboard_notes = []
        for item in selected_items:
            # Get full note data including children
            note_data = self._get_note_with_children(item.note_id)
            self.clipboard_notes.append(note_data)
        
        self.clipboard_operation = 'cut'
        
        main_window = self.window()
        if hasattr(main_window, 'status_bar'):
            main_window.status_bar.showMessage(f"Cut {len(selected_items)} note(s)", 2000)
    
    def copy_notes(self):
        """Copy selected notes to clipboard"""
        selected_items = [item for item in self.selectedItems() if isinstance(item, EditableTreeItem)]
        
        if not selected_items:
            return
        
        # Store note data for clipboard
        self.clipboard_notes = []
        for item in selected_items:
            # Get full note data including children
            note_data = self._get_note_with_children(item.note_id)
            self.clipboard_notes.append(note_data)
        
        self.clipboard_operation = 'copy'
        
        main_window = self.window()
        if hasattr(main_window, 'status_bar'):
            main_window.status_bar.showMessage(f"Copied {len(selected_items)} note(s)", 2000)
    
    def paste_notes(self):
        """Paste notes from clipboard"""
        if not self.clipboard_notes:
            main_window = self.window()
            if hasattr(main_window, 'status_bar'):
                main_window.status_bar.showMessage("Nothing to paste", 2000)
            return
        
        # Determine paste location
        current = self.currentItem()
        if isinstance(current, EditableTreeItem):
            target_parent_id = current.note_id
        else:
            target_parent_id = 1  # Root
        
        try:
            # Paste each note
            pasted_count = 0
            for note_data in self.clipboard_notes:
                new_id = self._create_note_tree(note_data, target_parent_id)
                if new_id:
                    pasted_count += 1
            
            # If this was a cut operation, delete the original notes
            if self.clipboard_operation == 'cut' and pasted_count > 0:
                for note_data in self.clipboard_notes:
                    self.db.delete_note(note_data['id'])
                
                # Clear clipboard after cut
                self.clipboard_notes = []
                self.clipboard_operation = None
                
                # Reload tree to show changes
                self.load_tree()
            
            main_window = self.window()
            if hasattr(main_window, 'status_bar'):
                operation = "Moved" if self.clipboard_operation == 'cut' else "Pasted"
                main_window.status_bar.showMessage(f"{operation} {pasted_count} note(s)", 2000)
                
        except Exception as e:
            main_window = self.window()
            if hasattr(main_window, 'status_bar'):
                main_window.status_bar.showMessage(f"Paste failed: {str(e)}", 3000)
    
    def _get_note_with_children(self, note_id):
        """Get note data including all children recursively"""
        note = self.db.get_note(note_id)
        if not note:
            return None
        
        # Get children
        children = self.db.get_children(note_id)
        child_data = []
        for child in children:
            child_tree = self._get_note_with_children(child['id'])
            if child_tree:
                child_data.append(child_tree)
        
        note['children'] = child_data
        return note
    
    def _create_note_tree(self, note_data, parent_id):
        """Create a note and all its children recursively"""
        if not note_data:
            return None
        
        # Create the note
        new_id = self.db.create_note(parent_id, note_data['content'])
        
        # If it was a task, recreate task status
        if note_data.get('task_status'):
            self.db.toggle_task(new_id)  # This makes it active
            if note_data['task_status'] == 'complete':
                self.db.toggle_task(new_id)  # This makes it complete
        
        # Create children
        for child_data in note_data.get('children', []):
            self._create_note_tree(child_data, new_id)
        
        return new_id
    
    def delete_empty_note_and_select_previous(self):
        """Delete current empty note and select the note above it"""
        current = self.currentItem()
        if not isinstance(current, EditableTreeItem) or current.note_id == 1:
            return  # Don't delete root
        
        # Find the item above current
        previous_item = self.itemAbove(current)
        
        # Finish editing
        self.finish_editing()
        
        # Delete from database
        self.db.delete_note(current.note_id)
        
        # Remove from tree directly
        parent_item = current.parent()
        if parent_item:
            parent_item.removeChild(current)
        else:
            root = self.invisibleRootItem()
            root.removeChild(current)
        
        # Select and start editing the previous item
        if previous_item and isinstance(previous_item, EditableTreeItem):
            self.setCurrentItem(previous_item)
            # Set preferred column to end of line when deleting
            self.preferred_column = float('inf')  # Will be clamped to line length
            self.start_editing_with_cursor_position(previous_item, 'end')
        elif parent_item:
            self.setCurrentItem(parent_item)
        elif self.topLevelItemCount() > 0:
            self.setCurrentItem(self.topLevelItem(0))

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Task Notes")
        self.setGeometry(100, 100, 1400, 900)
        
        # Initialize database with last opened database path
        last_db_path = self.load_last_database_path()
        self.db = DatabaseManager(last_db_path)
        
        # Check if git functionality is available and warn if not
        if not GIT_AVAILABLE:
            QMessageBox.warning(
                self, 
                "Git Version Control Not Available",
                "The pygit2 library is not installed, so version control features are disabled.\n\n"
                "Without git support:\n"
                "• No undo/redo functionality\n"
                "• No version history tracking\n"
                "• Changes are only saved to the database\n\n"
                "To enable full version control, install pygit2:\n"
                "pip install pygit2"
            )
        
        # Check if natural language date parsing is available and warn if not
        if not DATEUTIL_AVAILABLE:
            QMessageBox.warning(
                self,
                "Natural Language Date Parsing Not Available", 
                "The python-dateutil library is not installed, so natural language date parsing is disabled.\n\n"
                "Without dateutil support:\n"
                "• Cannot parse dates like 'tomorrow', 'next week', 'in 3 days'\n"
                "• Only ISO format dates work (2023-12-25)\n"
                "• Task scheduling is less flexible\n\n"
                "To enable natural language date parsing, install python-dateutil:\n"
                "pip install python-dateutil"
            )
        
        # Create main widget with splitter for resizable panes
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create layout
        layout = QVBoxLayout(central_widget)
        
        # Create status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")
        
        # Create horizontal splitter for resizable panes
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)
        
        # Create left panel with breadcrumbs and tree
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        # Create breadcrumb navigation
        self.breadcrumb_widget = self.create_breadcrumb_widget()
        left_layout.addWidget(self.breadcrumb_widget)
        
        # Create tree widget
        self.tree_widget = NoteTreeWidget(self.db)
        self.tree_widget.focus_changed_callback = self.on_tree_focus_changed
        left_layout.addWidget(self.tree_widget)
        
        splitter.addWidget(left_panel)
        
        # Create right side panel with vertical splitter
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(right_splitter)
        
        # Create details panel
        self.details_panel = self.create_details_panel()
        right_splitter.addWidget(self.details_panel)
        
        # Create task dashboard
        self.task_dashboard = self.create_task_dashboard()
        right_splitter.addWidget(self.task_dashboard)
        
        # Create history panel
        self.history_panel = self.create_history_panel()
        right_splitter.addWidget(self.history_panel)
        
        # Set initial splitter proportions - wider side panel for task table
        splitter.setSizes([600, 600])  # More space for right panel with task table
        right_splitter.setSizes([150, 150, 100])  # Space for details, tasks, and history
        
        # Connect tree selection to details update
        self.tree_widget.itemSelectionChanged.connect(self.update_details_panel)
        
        # Create menu bar
        self.create_menus()
        
        # Create toolbar
        self.create_toolbar()
        
        # Initial task dashboard update
        self.update_task_dashboard()
        
        # Set initial window title
        self.update_window_title()
        
        # Initialize recent files
        self.recent_files = self.load_recent_files()
        self.update_recent_files_menu()
        
        # Add current database to recent files
        self.add_to_recent_files(self.db.get_current_database_path())
        
        # Initialize history panel
        self.update_history_panel()
    
    def create_breadcrumb_widget(self):
        """Create the breadcrumb navigation widget"""
        widget = QWidget()
        widget.setFixedHeight(35)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        
        # Focus up button
        self.focus_up_button = QPushButton("↑")
        self.focus_up_button.setFixedSize(24, 24)
        self.focus_up_button.setToolTip("Focus on parent level")
        self.focus_up_button.clicked.connect(self.focus_tree_up)
        layout.addWidget(self.focus_up_button)
        
        # Breadcrumb area
        self.breadcrumb_layout = QHBoxLayout()
        self.breadcrumb_layout.setSpacing(2)
        layout.addLayout(self.breadcrumb_layout)
        
        layout.addStretch()
        
        # Root button (always visible)
        root_button = QPushButton("🏠")
        root_button.setFixedSize(24, 24)
        root_button.setToolTip("Go to root")
        root_button.clicked.connect(lambda: self.focus_tree_on(1))
        layout.addWidget(root_button)
        
        widget.setStyleSheet("""
            QWidget {
                background-color: #f8f8f8;
                border-bottom: 1px solid #ddd;
            }
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 3px;
                background-color: white;
                padding: 2px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #e6f3ff;
                border-color: #0078d4;
            }
            QPushButton:pressed {
                background-color: #d1e7dd;
            }
        """)
        
        return widget
    
    def on_tree_focus_changed(self, focused_root_id):
        """Handle tree focus change - update breadcrumbs"""
        # Use QTimer to prevent potential signal loops
        QTimer.singleShot(0, lambda: self.update_breadcrumbs(focused_root_id))
    
    def update_breadcrumbs(self, focused_root_id):
        """Update the breadcrumb navigation"""
        # Clear existing breadcrumb buttons
        while self.breadcrumb_layout.count():
            child = self.breadcrumb_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Update focus up button state
        self.focus_up_button.setEnabled(self.tree_widget.can_focus_up())
        
        # Get breadcrumb path
        breadcrumbs = self.tree_widget.get_focus_breadcrumbs()
        
        # Add breadcrumb buttons (skip the last one since that's where we are)
        for i, crumb in enumerate(breadcrumbs[:-1]):
            if i > 0:  # Add separator
                separator = QLabel("→")
                separator.setStyleSheet("color: #666; font-weight: bold;")
                self.breadcrumb_layout.addWidget(separator)
            
            button = QPushButton(crumb['content'])
            button.setMaximumWidth(150)
            button.clicked.connect(lambda checked, note_id=crumb['id']: self.focus_tree_on(note_id))
            button.setToolTip(f"Focus on: {crumb['content']}")
            self.breadcrumb_layout.addWidget(button)
        
        # Show current location (not clickable)
        if breadcrumbs:
            if len(breadcrumbs) > 1:
                separator = QLabel("→")
                separator.setStyleSheet("color: #666; font-weight: bold;")
                self.breadcrumb_layout.addWidget(separator)
            
            current = breadcrumbs[-1]
            current_label = QLabel(current['content'])
            current_label.setStyleSheet("font-weight: bold; color: #0078d4; padding: 4px;")
            current_label.setMaximumWidth(150)
            self.breadcrumb_layout.addWidget(current_label)
    
    def focus_tree_on(self, note_id):
        """Focus the tree on a specific note"""
        self.tree_widget.focus_on_subtree(note_id)
    
    def focus_tree_up(self):
        """Focus the tree up one level"""
        if self.tree_widget.focus_up():
            # Selection and details will be updated by the focus change callback
            pass
    
    def set_tree_depth(self, depth):
        """Set the maximum tree depth and reload if necessary"""
        old_depth = self.tree_widget.max_tree_depth
        self.tree_widget.max_tree_depth = depth
        
        # Update menu checkmarks
        for action in self.sender().parent().actions():
            if action.isCheckable():
                action_depth = 999 if "Unlimited" in action.text() else int(action.text().split()[0])
                action.setChecked(action_depth == depth)
        
        # Reload tree if we're increasing depth or if current tree might be truncated
        if depth > old_depth or old_depth <= 10:
            self.tree_widget.load_tree()
            self.status_bar.showMessage(f"Tree depth set to {depth if depth < 999 else 'unlimited'} levels", 2000)
    
    def set_history_date(self, date_obj):
        """Set the history date and update the panel"""
        self.history_date.setDate(date_obj)
        # The dateChanged signal will automatically trigger update_history_panel
    
    def update_history_panel(self):
        """Update the history panel with notes from the selected date"""
        if not hasattr(self, 'history_list'):
            return
        
        # Get selected date and filter
        selected_qdate = self.history_date.date()
        date_str = selected_qdate.toString('yyyy-MM-dd')
        
        filter_text = self.history_filter.currentText()
        if filter_text == "Created":
            activity_type = "created"
        elif filter_text == "Modified":
            activity_type = "modified"
        else:
            activity_type = "all"
        
        # Get notes for the selected date
        notes = self.db.get_notes_by_date(date_str, activity_type)
        
        # Clear and populate the list
        self.history_list.clear()
        
        if not notes:
            item = QListWidgetItem("No activity on this date")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            item.setForeground(QColor("gray"))
            self.history_list.addItem(item)
            return
        
        for note in notes:
            # Create display text
            content = note['content'][:50] + "..." if len(note['content']) > 50 else note['content']
            if not content.strip():
                content = "(empty note)"
            
            # Add task indicator if it's a task
            if note['task_status']:
                if note['task_status'] == 'complete':
                    content = "☑ " + content
                elif note['task_status'] == 'active':
                    content = "☐ " + content
            
            # Format time
            activity_time = note['activity_time']
            try:
                if 'T' in activity_time:
                    time_part = activity_time.split('T')[1].split('.')[0]  # Get HH:MM:SS
                    time_part = time_part[:5]  # Just HH:MM
                else:
                    time_part = activity_time[-8:-3]  # Last 5 chars should be HH:MM
            except:
                time_part = "--:--"
            
            # Create list item with appropriate activity label
            if note['activity_type'] == 'created':
                activity_label = "📝"
            elif note['activity_type'] == 'completed':
                activity_label = "✅"  # Special marker for task completions
                content = "☑ " + content  # Add completed task indicator
            else:  # modified
                activity_label = "✏️"
            
            item_text = f"{activity_label} {time_part} - {content}"
            
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, note['id'])  # Store note ID
            self.history_list.addItem(item)
    
    def on_history_item_clicked(self, item):
        """Handle clicks on history items - navigate to the note"""
        note_id = item.data(Qt.ItemDataRole.UserRole)
        if note_id is None:
            return
        
        # Find and select the note in the tree
        self.find_and_select_note(note_id)
    
    def find_and_select_note(self, note_id):
        """Find a note by ID and select it, focusing on its subtree if necessary"""
        # First try to find it in the current tree view
        found_item = self.find_item_in_tree(note_id)
        
        if found_item:
            # Found in current view - select it
            self.tree_widget.clearSelection()
            self.tree_widget.setCurrentItem(found_item)
            found_item.setSelected(True)
            self.tree_widget.scrollToItem(found_item)
            return
        
        # Not found in current view - get the note and focus on its parent's subtree
        note_data = self.db.get_note(note_id)
        if not note_data:
            self.status_bar.showMessage(f"Note {note_id} not found", 3000)
            return
        
        # If we're in a focused subtree, try going to root first
        if self.tree_widget.get_focused_root() != 1:
            self.tree_widget.focus_on_subtree(1)  # Go to root
            # Try again to find it
            found_item = self.find_item_in_tree(note_id)
            if found_item:
                self.tree_widget.clearSelection()
                self.tree_widget.setCurrentItem(found_item)
                found_item.setSelected(True)
                self.tree_widget.scrollToItem(found_item)
                return
        
        # Still not found - the note might be in a collapsed branch
        self.status_bar.showMessage(f"Note found but may be in collapsed branch (ID: {note_id})", 3000)
    
    def find_item_in_tree(self, note_id):
        """Recursively search for a tree item by note ID"""
        def search_item(item):
            if isinstance(item, EditableTreeItem) and item.note_id == note_id:
                return item
            
            for i in range(item.childCount()):
                result = search_item(item.child(i))
                if result:
                    return result
            return None
        
        # Search through all top-level items
        for i in range(self.tree_widget.topLevelItemCount()):
            result = search_item(self.tree_widget.topLevelItem(i))
            if result:
                return result
        
        return None
    
    def rebuild_note_paths(self):
        """Rebuild all note paths for consistency"""
        reply = QMessageBox.question(
            self, 
            "Rebuild Paths", 
            "Rebuild all note paths? This will fix any path inconsistencies but may take a moment.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.db.rebuild_paths()
                self.tree_widget.load_tree()  # Reload tree to reflect changes
                self.update_details_panel()   # Refresh details
                self.status_bar.showMessage("Note paths rebuilt successfully", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to rebuild paths: {str(e)}")
                self.status_bar.showMessage(f"Path rebuild failed: {str(e)}", 3000)
    
    def create_new_note_from_menu(self):
        """Create new note from menu action"""
        self.tree_widget.create_new_note()
    
    def new_database(self):
        """Create a new database file"""
        # Ask for confirmation if there are unsaved changes
        reply = QMessageBox.question(
            self, 
            "New Database", 
            "Create a new database? This will close the current database.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Ask for new database location
        import os
        default_path = os.path.expanduser("~/notes.db")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "New Database",
            default_path,
            "Database files (*.db);;All files (*)"
        )
        
        if file_path:
            try:
                # Finish any current editing
                if self.tree_widget.editing_item:
                    self.tree_widget.finish_editing()
                
                # Create new database
                import os
                if os.path.exists(file_path):
                    os.remove(file_path)  # Remove existing file to create fresh
                
                self.db = DatabaseManager(file_path)
                self.tree_widget.db = self.db
                
                # Reload the tree
                self.tree_widget.load_tree()
                self.update_details_panel()
                
                # Update window title and add to recent files
                self.update_window_title()
                self.add_to_recent_files(file_path)
                self.save_last_database_path(file_path)
                self.status_bar.showMessage(f"Created new database: {file_path}", 3000)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not create database: {str(e)}")
    
    def open_database(self):
        """Open an existing database file"""
        import os
        home_dir = os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Database",
            home_dir,
            "Database files (*.db);;All files (*)"
        )
        
        if file_path:
            try:
                # Finish any current editing
                if self.tree_widget.editing_item:
                    self.tree_widget.finish_editing()
                
                # Load the database
                self.db.load_database(file_path)
                self.tree_widget.db = self.db
                
                # Reload the tree
                self.tree_widget.load_tree()
                self.update_details_panel()
                
                # Update window title and add to recent files
                import os
                self.update_window_title()
                self.add_to_recent_files(file_path)
                self.save_last_database_path(file_path)
                self.status_bar.showMessage(f"Opened database: {file_path}", 3000)
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open database: {str(e)}")
    
    def save_database(self):
        """Save current database (no-op since it auto-saves)"""
        # The database is automatically saved with each change,
        # so this is mainly for user feedback
        current_path = self.db.get_current_database_path()
        import os
        self.status_bar.showMessage(f"Database saved: {os.path.basename(current_path)}", 2000)
    
    def save_database_as(self):
        """Save database to a new file"""
        current_path = self.db.get_current_database_path()
        import os
        default_name = os.path.splitext(os.path.basename(current_path))[0] + "_copy.db"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Database As",
            default_name,
            "Database files (*.db);;All files (*)"
        )
        
        if file_path:
            try:
                # Finish any current editing
                if self.tree_widget.editing_item:
                    self.tree_widget.finish_editing()
                
                # Save to new location
                success = self.db.save_database_as(file_path)
                
                if success:
                    # Update window title and add to recent files
                    self.update_window_title()
                    self.add_to_recent_files(file_path)
                    self.save_last_database_path(file_path)
                    self.status_bar.showMessage(f"Database saved as: {file_path}", 3000)
                else:
                    QMessageBox.warning(self, "Warning", "Database save may have failed")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save database: {str(e)}")
    
    def update_window_title(self):
        """Update window title with current database name"""
        import os
        db_name = os.path.basename(self.db.get_current_database_path())
        self.setWindowTitle(f"Task Notes - {db_name}")
    
    def load_recent_files(self):
        """Load recent files from settings"""
        try:
            import json
            with open("settings.json", "r") as f:
                settings = json.load(f)
                return settings.get("recent_files", [])
        except Exception:
            return []
    
    def save_recent_files(self):
        """Save recent files to settings"""
        try:
            import json
            
            # Load existing settings
            settings = {}
            try:
                with open("settings.json", "r") as f:
                    settings = json.load(f)
            except Exception:
                pass
            
            # Update recent files
            settings["recent_files"] = self.recent_files
            
            # Save settings
            with open("settings.json", "w") as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Could not save recent files: {e}")
    
    def load_last_database_path(self):
        """Load last opened database path from settings"""
        try:
            import json
            import os
            with open("settings.json", "r") as f:
                settings = json.load(f)
                default_path = os.path.expanduser("~/notes.db")
                return settings.get("last_database_path", default_path)
        except Exception:
            import os
            return os.path.expanduser("~/notes.db")
    
    def save_last_database_path(self, db_path):
        """Save last opened database path to settings"""
        try:
            import json
            
            # Load existing settings
            settings = {}
            try:
                with open("settings.json", "r") as f:
                    settings = json.load(f)
            except Exception:
                pass
            
            # Update last database path
            settings["last_database_path"] = db_path
            
            # Save settings
            with open("settings.json", "w") as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Could not save last database path: {e}")
    
    def add_to_recent_files(self, file_path):
        """Add a file to the recent files list"""
        import os
        file_path = os.path.abspath(file_path)
        
        # Remove if already in list
        if file_path in self.recent_files:
            self.recent_files.remove(file_path)
        
        # Add to beginning
        self.recent_files.insert(0, file_path)
        
        # Keep only last 10 files
        self.recent_files = self.recent_files[:10]
        
        # Save and update menu
        self.save_recent_files()
        self.update_recent_files_menu()
    
    def update_recent_files_menu(self):
        """Update the recent files menu"""
        self.recent_files_menu.clear()
        
        if not self.recent_files:
            action = QAction("No recent files", self)
            action.setEnabled(False)
            self.recent_files_menu.addAction(action)
            return
        
        import os
        for file_path in self.recent_files:
            if os.path.exists(file_path):
                file_name = os.path.basename(file_path)
                action = QAction(file_name, self)
                action.setStatusTip(file_path)
                action.triggered.connect(lambda checked, path=file_path: self.open_recent_file(path))
                self.recent_files_menu.addAction(action)
        
        if self.recent_files:
            self.recent_files_menu.addSeparator()
            clear_action = QAction("Clear Recent Files", self)
            clear_action.triggered.connect(self.clear_recent_files)
            self.recent_files_menu.addAction(clear_action)
    
    def open_recent_file(self, file_path):
        """Open a recent file"""
        import os
        if not os.path.exists(file_path):
            QMessageBox.warning(self, "File Not Found", f"The file {file_path} no longer exists.")
            # Remove from recent files
            if file_path in self.recent_files:
                self.recent_files.remove(file_path)
                self.save_recent_files()
                self.update_recent_files_menu()
            return
        
        try:
            # Finish any current editing
            if self.tree_widget.editing_item:
                self.tree_widget.finish_editing()
            
            # Load the database
            self.db.load_database(file_path)
            self.tree_widget.db = self.db
            
            # Reload the tree
            self.tree_widget.load_tree()
            self.update_details_panel()
            
            # Update window title and recent files
            self.update_window_title()
            self.add_to_recent_files(file_path)
            self.save_last_database_path(file_path)
            self.status_bar.showMessage(f"Opened: {os.path.basename(file_path)}", 3000)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open database: {str(e)}")
    
    def clear_recent_files(self):
        """Clear the recent files list"""
        self.recent_files = []
        self.save_recent_files()
        self.update_recent_files_menu()
    
    def manual_refresh(self):
        """Manually refresh the tree (for debugging)"""
        print("=== MANUAL REFRESH TRIGGERED ===")
        
        # Store current expansion states
        expansion_states = {}
        selected_note_ids = []
        
        def store_expansion_state(item):
            if isinstance(item, EditableTreeItem):
                expansion_states[item.note_id] = item.isExpanded()
                if item.isSelected():
                    selected_note_ids.append(item.note_id)
            for i in range(item.childCount()):
                store_expansion_state(item.child(i))
        
        for i in range(self.tree_widget.topLevelItemCount()):
            store_expansion_state(self.tree_widget.topLevelItem(i))
        
        print(f"Stored states for {len(expansion_states)} items, {len(selected_note_ids)} selected")
        
        # Do full tree reload
        self.tree_widget.load_tree()
        
        # Restore expansion states
        def restore_expansion_state(item):
            if isinstance(item, EditableTreeItem) and item.note_id in expansion_states:
                item.setExpanded(expansion_states[item.note_id])
            for i in range(item.childCount()):
                restore_expansion_state(item.child(i))
        
        for i in range(self.tree_widget.topLevelItemCount()):
            restore_expansion_state(self.tree_widget.topLevelItem(i))
        
        # Restore selection
        self.tree_widget.restore_selection_by_ids(selected_note_ids)
        
        print("Manual refresh complete!")
        
        # Update status bar
        if hasattr(self, 'status_bar'):
            self.status_bar.showMessage("Tree refreshed manually", 2000)
    
    def create_menus(self):
        """Create menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        # New/Open/Save actions
        new_db_action = QAction("New Database", self)
        new_db_action.setShortcut("Ctrl+N")
        new_db_action.triggered.connect(self.new_database)
        file_menu.addAction(new_db_action)
        
        open_action = QAction("Open Database...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_database)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        save_action = QAction("Save Database", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_database)
        file_menu.addAction(save_action)
        
        save_as_action = QAction("Save Database As...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_database_as)
        file_menu.addAction(save_as_action)
        
        file_menu.addSeparator()
        
        # Recent files menu (will be populated dynamically)
        self.recent_files_menu = file_menu.addMenu("Recent Files")
        
        file_menu.addSeparator()
        
        new_action = QAction("New Note", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self.create_new_note_from_menu)
        file_menu.addAction(new_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Edit menu
        edit_menu = menubar.addMenu("Edit")
        
        # Add undo/redo if git is available
        if GIT_AVAILABLE and self.db.git_vc:
            undo_action = QAction("Undo", self)
            undo_action.setShortcut("Ctrl+Z")
            undo_action.triggered.connect(self.undo)
            edit_menu.addAction(undo_action)
            
            redo_action = QAction("Redo", self)
            redo_action.setShortcut("Ctrl+Y")
            redo_action.triggered.connect(self.redo)
            edit_menu.addAction(redo_action)
            
            edit_menu.addSeparator()
        
        # Cut/Copy/Paste
        cut_action = QAction("Cut", self)
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(lambda: self.tree_widget.cut_notes())
        edit_menu.addAction(cut_action)
        
        copy_action = QAction("Copy", self)
        copy_action.setShortcut("Ctrl+C")  
        copy_action.triggered.connect(lambda: self.tree_widget.copy_notes())
        edit_menu.addAction(copy_action)
        
        paste_action = QAction("Paste", self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(lambda: self.tree_widget.paste_notes())
        edit_menu.addAction(paste_action)
        
        edit_menu.addSeparator()
        
        delete_action = QAction("Delete", self)
        delete_action.setShortcut("Del")
        delete_action.triggered.connect(lambda: self.tree_widget.delete_current_note())
        edit_menu.addAction(delete_action)
        
        edit_menu.addSeparator()
        
        task_action = QAction("Toggle Task", self)
        task_action.setShortcut("Ctrl+Space")
        task_action.triggered.connect(lambda: self.tree_widget.toggle_task())
        edit_menu.addAction(task_action)
        
        # View menu (combined)
        view_menu = menubar.addMenu("View")
        
        # Refresh action
        refresh_action = QAction("Refresh Tree", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.manual_refresh)
        view_menu.addAction(refresh_action)
        
        view_menu.addSeparator()
        
        # Font size submenu
        font_menu = view_menu.addMenu("Font Size")
        
        font_smaller_action = QAction("Smaller", self)
        font_smaller_action.setShortcut("Ctrl+-")
        font_smaller_action.triggered.connect(self.decrease_font_size)
        font_menu.addAction(font_smaller_action)
        
        font_larger_action = QAction("Larger", self)
        font_larger_action.setShortcut("Ctrl+=")
        font_larger_action.triggered.connect(self.increase_font_size)
        font_menu.addAction(font_larger_action)
        
        font_menu.addSeparator()
        
        font_reset_action = QAction("Reset to Default", self)
        font_reset_action.setShortcut("Ctrl+0")
        font_reset_action.triggered.connect(self.reset_font_size)
        font_menu.addAction(font_reset_action)
        
        # Add git history if available
        # Navigation actions
        view_menu.addSeparator()
        
        focus_up_action = QAction("Focus Up", self)
        focus_up_action.setShortcut("Alt+Up")
        focus_up_action.triggered.connect(self.focus_tree_up)
        view_menu.addAction(focus_up_action)
        
        focus_root_action = QAction("Focus on Root", self)
        focus_root_action.setShortcut("Alt+Home")
        focus_root_action.triggered.connect(lambda: self.focus_tree_on(1))
        view_menu.addAction(focus_root_action)
        
        view_menu.addSeparator()
        
        # Tree depth menu
        depth_menu = view_menu.addMenu("Tree Depth Limit")
        
        for depth in [5, 10, 15, 20, 999]:
            depth_label = "Unlimited" if depth == 999 else f"{depth} levels"
            depth_action = QAction(depth_label, self)
            depth_action.setCheckable(True)
            depth_action.setChecked(depth == self.tree_widget.max_tree_depth)
            depth_action.triggered.connect(lambda checked, d=depth: self.set_tree_depth(d))
            depth_menu.addAction(depth_action)
        
        # Debug/maintenance actions
        view_menu.addSeparator()
        
        rebuild_paths_action = QAction("Rebuild Note Paths", self)
        rebuild_paths_action.triggered.connect(self.rebuild_note_paths)
        view_menu.addAction(rebuild_paths_action)
        
        if GIT_AVAILABLE and self.db.git_vc:
            view_menu.addSeparator()
            
            history_action = QAction("Show Version History", self)
            history_action.setShortcut("Ctrl+H")
            history_action.triggered.connect(self.show_git_history)
            view_menu.addAction(history_action)
        
        # Store default font size and load saved font size
        self.default_font_size = self.tree_widget.font().pointSize()
        self.load_font_size()
    
    def create_toolbar(self):
        """Create the application toolbar with undo/redo buttons"""
        toolbar = self.addToolBar("Main Toolbar")
        toolbar.setObjectName("MainToolBar")
        
        # Undo button
        if GIT_AVAILABLE and self.db.git_vc:
            undo_action = QAction("↶ Undo", self)
            undo_action.setShortcut("Ctrl+Z")
            undo_action.setToolTip("Undo last change (Ctrl+Z)")
            undo_action.triggered.connect(self.undo)
            toolbar.addAction(undo_action)
            
            # Redo button
            redo_action = QAction("↷ Redo", self)
            redo_action.setShortcut("Ctrl+Y")
            redo_action.setToolTip("Redo last undone change (Ctrl+Y)")
            redo_action.triggered.connect(self.redo)
            toolbar.addAction(redo_action)
            
            # Add separator
            toolbar.addSeparator()
        
        # Add new note button
        new_note_action = QAction("📝 New Note", self)
        new_note_action.setToolTip("Create new note")
        new_note_action.triggered.connect(self.tree_widget.create_new_note)
        toolbar.addAction(new_note_action)
        
        # Toggle task button
        toggle_task_action = QAction("☐ Task", self)
        toggle_task_action.setToolTip("Toggle task status")
        toggle_task_action.triggered.connect(self.tree_widget.toggle_task)
        toolbar.addAction(toggle_task_action)
    
    def create_details_panel(self):
        """Create the note details panel"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title with child count on same line
        title_layout = QHBoxLayout()
        title = QLabel("Note Details")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title)
        
        # Child count (will be set later)
        self.detail_children_label = QLabel("Children: -")
        title_layout.addWidget(self.detail_children_label)
        title_layout.addStretch()
        layout.addLayout(title_layout)
        
        # Note path (breadcrumb) - no separate label
        self.detail_path_label = QLabel("-")
        self.detail_path_label.setWordWrap(True)
        self.detail_path_label.setStyleSheet("font-style: italic; margin-bottom: 5px;")
        layout.addWidget(self.detail_path_label)
        
        # Task checkbox
        self.detail_task_checkbox = QCheckBox("Is Task")
        self.detail_task_checkbox.clicked.connect(self.on_task_checkbox_changed)
        layout.addWidget(self.detail_task_checkbox)
        
        # Created and modified dates on same line
        dates_layout = QHBoxLayout()
        self.detail_created_label = QLabel("Created: -")
        self.detail_modified_label = QLabel("Modified: -")
        dates_layout.addWidget(self.detail_created_label)
        dates_layout.addWidget(self.detail_modified_label)
        dates_layout.addStretch()
        layout.addLayout(dates_layout)
        
        # Task fields (shown only for tasks)
        self.task_fields_widget = QWidget()
        self.task_fields_layout = QVBoxLayout(self.task_fields_widget)
        self.task_fields_layout.setContentsMargins(0, 5, 0, 5)
        
        # Task status
        task_status_layout = QHBoxLayout()
        task_status_layout.addWidget(QLabel("Status:"))
        self.detail_task_status = QLabel("-")
        task_status_layout.addWidget(self.detail_task_status)
        task_status_layout.addStretch()
        self.task_fields_layout.addLayout(task_status_layout)
        
        # Start date/time
        start_layout = QHBoxLayout()
        start_layout.addWidget(QLabel("Start:"))
        self.detail_start_date = QLineEdit()
        self.detail_start_date.setPlaceholderText("e.g., 'tomorrow 9am', 'next tuesday'")
        self.detail_start_date.editingFinished.connect(self.update_start_date)
        start_layout.addWidget(self.detail_start_date)
        self.task_fields_layout.addLayout(start_layout)
        
        # Due date/time  
        due_layout = QHBoxLayout()
        due_layout.addWidget(QLabel("Due:"))
        self.detail_due_date = QLineEdit()
        self.detail_due_date.setPlaceholderText("e.g., 'friday 5pm', 'in 3 days'")
        self.detail_due_date.editingFinished.connect(self.update_due_date)
        due_layout.addWidget(self.detail_due_date)
        self.task_fields_layout.addLayout(due_layout)
        
        # Priority
        priority_layout = QHBoxLayout()
        priority_layout.addWidget(QLabel("Priority:"))
        self.detail_priority = QSpinBox()
        self.detail_priority.setRange(0, 10)
        self.detail_priority.setValue(0)
        self.detail_priority.setSpecialValueText("None")
        self.detail_priority.setToolTip("Priority (0=None, 1=Low, 5=Medium, 10=High)")
        self.detail_priority.valueChanged.connect(self.update_priority)
        priority_layout.addWidget(self.detail_priority)
        priority_layout.addStretch()
        self.task_fields_layout.addLayout(priority_layout)
        
        # Completed at
        completed_layout = QHBoxLayout()
        completed_layout.addWidget(QLabel("Completed:"))
        self.detail_completed_at = QLabel("-")
        self.detail_completed_at.setStyleSheet("color: #666; font-style: italic;")
        completed_layout.addWidget(self.detail_completed_at)
        completed_layout.addStretch()
        self.task_fields_layout.addLayout(completed_layout)
        
        layout.addWidget(self.task_fields_widget)
        self.task_fields_widget.hide()  # Hidden by default
        
        # Content preview (no separate label)
        self.detail_content = QTextEdit()
        self.detail_content.setMaximumHeight(120)
        self.detail_content.setReadOnly(True)
        layout.addWidget(self.detail_content)
        
        layout.addStretch()
        
        widget.setStyleSheet("""
            QWidget {
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #f9f9f9;
            }
            QLabel {
                border: none;
                background: transparent;
                padding: 2px;
            }
            QTextEdit {
                border: 1px solid #ddd;
                background-color: white;
                border-radius: 3px;
            }
        """)
        
        return widget
    
    def create_task_dashboard(self):
        """Create the task dashboard panel"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title with task counts on same line
        title_layout = QHBoxLayout()
        title = QLabel("Task Dashboard")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title)
        
        self.task_active_label = QLabel("Active Tasks: 0")
        self.task_completed_today_label = QLabel("Completed Today: 0")
        
        title_layout.addWidget(self.task_active_label)
        title_layout.addWidget(self.task_completed_today_label)
        title_layout.addStretch()
        
        # Toggle for subtree vs all tasks
        self.subtree_tasks_only = QCheckBox("Subtree Only")
        self.subtree_tasks_only.setToolTip("Show only tasks in the current focused subtree")
        self.subtree_tasks_only.stateChanged.connect(self.update_task_dashboard)
        title_layout.addWidget(self.subtree_tasks_only)
        
        layout.addLayout(title_layout)
        
        layout.addWidget(QLabel(""))  # Spacer
        
        # Active tasks table (label removed for space efficiency)
        self.active_tasks_table = QTableWidget()
        self.active_tasks_table.setColumnCount(4)
        self.active_tasks_table.setHorizontalHeaderLabels(["Task", "Start Date", "Due Date", "Priority"])
        
        # Enable sorting and editing
        self.active_tasks_table.setSortingEnabled(True)
        self.active_tasks_table.itemChanged.connect(self.on_task_table_item_changed)
        self.active_tasks_table.itemClicked.connect(self.on_task_table_item_clicked)
        
        # Adjust column widths
        header = self.active_tasks_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.resizeSection(0, 200)  # Task column wider
        header.resizeSection(1, 100)  # Start Date
        header.resizeSection(2, 100)  # Due Date
        header.resizeSection(3, 80)   # Priority
        
        # Add smart sort button
        smart_sort_button = QPushButton("🔄 Smart Sort")
        smart_sort_button.setToolTip("Return to intelligent categorized sorting")
        smart_sort_button.setMaximumWidth(120)
        smart_sort_button.clicked.connect(self.restore_smart_sort)
        
        # Create horizontal layout for button to prevent stretching
        button_layout = QHBoxLayout()
        button_layout.addWidget(smart_sort_button)
        button_layout.addStretch()  # Push button to left
        
        button_widget = QWidget()
        button_widget.setLayout(button_layout)
        layout.addWidget(button_widget)
        
        layout.addWidget(self.active_tasks_table)
        
        widget.setStyleSheet("""
            QWidget {
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #f0f8ff;
            }
            QLabel {
                border: none;
                background: transparent;
                padding: 2px;
            }
            QTableWidget {
                border: 1px solid #ddd;
                background-color: white;
                border-radius: 3px;
                gridline-color: #eee;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 4px;
                border: 1px solid #ccc;
                font-weight: bold;
            }
            QPushButton {
                background-color: #e0e0e0;
                border: 2px solid #aaa;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                color: #333;
            }
            QPushButton:hover {
                background-color: #d0d0d0;
                border-color: #888;
            }
            QPushButton:pressed {
                background-color: #c0c0c0;
                border-color: #666;
            }
        """)
        
        return widget
    
    def create_history_panel(self):
        """Create the history panel"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Title with date picker
        title_layout = QHBoxLayout()
        title = QLabel("History")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_layout.addWidget(title)
        
        # Date picker
        from datetime import date
        self.history_date = QDateEdit()
        self.history_date.setDate(date.today())
        self.history_date.setCalendarPopup(True)
        self.history_date.dateChanged.connect(self.update_history_panel)
        title_layout.addWidget(self.history_date)
        
        title_layout.addStretch()
        layout.addLayout(title_layout)
        
        # Activity filter
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Show:"))
        
        self.history_filter = QComboBox()
        self.history_filter.addItems(["All Activity", "Created", "Modified"])
        self.history_filter.currentTextChanged.connect(self.update_history_panel)
        filter_layout.addWidget(self.history_filter)
        
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # History list
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self.on_history_item_clicked)
        layout.addWidget(self.history_list)
        
        # Quick date buttons
        quick_dates_layout = QHBoxLayout()
        
        today_btn = QPushButton("Today")
        today_btn.clicked.connect(lambda: self.set_history_date(date.today()))
        quick_dates_layout.addWidget(today_btn)
        
        yesterday_btn = QPushButton("Yesterday")
        from datetime import timedelta
        yesterday_btn.clicked.connect(lambda: self.set_history_date(date.today() - timedelta(days=1)))
        quick_dates_layout.addWidget(yesterday_btn)
        
        quick_dates_layout.addStretch()
        layout.addLayout(quick_dates_layout)
        
        widget.setStyleSheet("""
            QWidget {
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: #f8f8ff;
            }
            QLabel {
                border: none;
                background: transparent;
                padding: 2px;
            }
            QListWidget {
                border: 1px solid #ddd;
                background-color: white;
                border-radius: 3px;
            }
            QListWidget::item {
                padding: 6px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:hover {
                background-color: #e6f3ff;
            }
            QListWidget::item:selected {
                background-color: #cce7ff;
            }
            QPushButton {
                border: 1px solid #ccc;
                border-radius: 3px;
                background-color: white;
                padding: 4px 8px;
                min-width: 60px;
            }
            QPushButton:hover {
                background-color: #e6f3ff;
                border-color: #0078d4;
            }
        """)
        
        return widget
    
    def get_breadcrumb_path(self, note_data):
        """Get breadcrumb path for a note"""
        path = note_data.get('path', '')
        
        if not path:
            return "Root"
        
        # Split path (e.g., "1.23.45" -> ["1", "23", "45"])
        # The path should include ALL ancestors from root to this note
        path_ids = path.split('.')
        breadcrumbs = []
        
        for note_id_str in path_ids:
            try:
                note_id_int = int(note_id_str)
                note = self.db.get_note(note_id_int)
                if note:
                    content = note['content'][:12] + "..." if len(note['content']) > 12 else note['content']
                    if not content.strip():
                        content = "(empty)"
                    breadcrumbs.append(content)
                else:
                    breadcrumbs.append(f"#{note_id_str}")
            except Exception:
                breadcrumbs.append(f"#{note_id_str}")
        
        return " → ".join(breadcrumbs)

    def update_details_panel(self):
        """Update the details panel with selected note info"""
        selected_items = [item for item in self.tree_widget.selectedItems() 
                         if isinstance(item, EditableTreeItem)]
        
        if len(selected_items) == 1:
            item = selected_items[0]
            note_data = item.note_data
            
            # Update breadcrumb path
            self.detail_path_label.setText(self.get_breadcrumb_path(note_data))
            
            # Format dates with proper timezone conversion
            created = note_data.get('created_at', '-')
            if created != '-':
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    # If no timezone info, assume it's UTC and convert to local
                    if dt.tzinfo is None:
                        # Assume UTC for database timestamps
                        from datetime import timezone
                        dt = dt.replace(tzinfo=timezone.utc)
                    # Convert to local time
                    local_dt = dt.astimezone()
                    created = local_dt.strftime('%m/%d/%Y %I:%M %p')
                except:
                    # Fallback to simple format if parsing fails
                    created = created.replace('T', ' ').split('.')[0]
            self.detail_created_label.setText(f"Created: {created}")
            
            modified = note_data.get('modified_at', '-')
            if modified != '-':
                try:
                    dt = datetime.fromisoformat(modified.replace('Z', '+00:00'))
                    # If no timezone info, assume it's UTC and convert to local
                    if dt.tzinfo is None:
                        # Assume UTC for database timestamps
                        from datetime import timezone
                        dt = dt.replace(tzinfo=timezone.utc)
                    # Convert to local time
                    local_dt = dt.astimezone()
                    modified = local_dt.strftime('%m/%d/%Y %I:%M %p')
                except:
                    # Fallback to simple format if parsing fails
                    modified = modified.replace('T', ' ').split('.')[0]
            self.detail_modified_label.setText(f"Modified: {modified}")
            
            # Handle task checkbox and fields
            task_status = note_data.get('task_status', None)
            
            # Update checkbox state (block signals to prevent loops)
            self.detail_task_checkbox.blockSignals(True)
            self.detail_task_checkbox.setChecked(task_status is not None)
            self.detail_task_checkbox.setEnabled(True)
            self.detail_task_checkbox.blockSignals(False)
            
            if task_status:
                # Show task fields
                self.task_fields_widget.show()
                self.detail_task_status.setText(task_status.title())
                
                # Format and display dates in local timezone
                start_date = note_data.get('start_date')
                if start_date:
                    try:
                        dt = datetime.fromisoformat(start_date)
                        # If no timezone info, assume it's local time
                        if dt.tzinfo is None:
                            # Display as local time
                            self.detail_start_date.setText(dt.strftime('%m/%d/%Y %I:%M %p'))
                        else:
                            # Convert to local time
                            local_dt = dt.astimezone()
                            self.detail_start_date.setText(local_dt.strftime('%m/%d/%Y %I:%M %p'))
                    except:
                        self.detail_start_date.setText(start_date)
                else:
                    self.detail_start_date.setText('')
                
                due_date = note_data.get('due_date') 
                if due_date:
                    try:
                        dt = datetime.fromisoformat(due_date)
                        # If no timezone info, assume it's local time
                        if dt.tzinfo is None:
                            # Display as local time
                            self.detail_due_date.setText(dt.strftime('%m/%d/%Y %I:%M %p'))
                        else:
                            # Convert to local time
                            local_dt = dt.astimezone()
                            self.detail_due_date.setText(local_dt.strftime('%m/%d/%Y %I:%M %p'))
                    except:
                        self.detail_due_date.setText(due_date)
                else:
                    self.detail_due_date.setText('')
                
                # Completed at
                completed_at = note_data.get('completed_at')
                if completed_at and task_status == 'complete':
                    try:
                        dt = datetime.fromisoformat(completed_at.replace('Z', '+00:00'))
                        # If no timezone info, assume it's UTC and convert to local
                        if dt.tzinfo is None:
                            from datetime import timezone
                            dt = dt.replace(tzinfo=timezone.utc)
                        # Convert to local time
                        local_dt = dt.astimezone()
                        self.detail_completed_at.setText(local_dt.strftime('%m/%d/%Y %I:%M %p'))
                    except:
                        # Fallback to simple format if parsing fails
                        self.detail_completed_at.setText(completed_at.replace('T', ' ').split('.')[0])
                else:
                    self.detail_completed_at.setText('-')
                
                # Priority (block signals to prevent loops)
                priority = note_data.get('priority', 0) or 0
                self.detail_priority.blockSignals(True)
                self.detail_priority.setValue(priority)
                self.detail_priority.blockSignals(False)
                
                # Store current note ID for updates
                self.current_task_id = note_data['id']
            else:
                # Hide task fields for non-tasks
                self.task_fields_widget.hide()
                self.current_task_id = None
            
            # Content preview
            content = note_data.get('content', '')
            self.detail_content.setText(content)
            
            # Count children
            children = self.db.get_children(note_data['id'])
            self.detail_children_label.setText(f"Children: {len(children)}")
            
        elif len(selected_items) > 1:
            # Multiple selection
            self.detail_path_label.setText("Multiple notes selected")
            self.detail_created_label.setText("Created: -")
            self.detail_modified_label.setText("Modified: -")
            self.detail_task_checkbox.blockSignals(True)
            self.detail_task_checkbox.setChecked(False)
            self.detail_task_checkbox.setEnabled(False)
            self.detail_task_checkbox.blockSignals(False)
            self.task_fields_widget.hide()
            self.detail_content.setText("")
            self.detail_children_label.setText("Children: -")
            self.current_task_id = None
        else:
            # No selection
            self.detail_path_label.setText("-")
            self.detail_created_label.setText("Created: -")
            self.detail_modified_label.setText("Modified: -")
            self.detail_task_checkbox.blockSignals(True)
            self.detail_task_checkbox.setChecked(False)
            self.detail_task_checkbox.setEnabled(False)
            self.detail_task_checkbox.blockSignals(False)
            self.task_fields_widget.hide()
            self.detail_content.setText("")
            self.detail_children_label.setText("Children: -")
            self.current_task_id = None
        
        # Update task dashboard
        self.update_task_dashboard()
    
    def on_task_checkbox_changed(self):
        """Handle task checkbox state changes"""
        # Prevent loops during programmatic updates
        if hasattr(self, '_updating_checkbox') and self._updating_checkbox:
            return
        
        # Get current selection
        selected_items = [item for item in self.tree_widget.selectedItems() 
                         if isinstance(item, EditableTreeItem)]
        
        if len(selected_items) != 1:
            return  # Only work with single selection
        
        self._updating_checkbox = True
        try:
            # Toggle the task status by calling the tree widget's toggle_task method
            self.tree_widget.toggle_task()
        finally:
            self._updating_checkbox = False
        
        # Update the details panel AFTER clearing the flag to ensure checkbox state updates
        self.update_details_panel()
    
    def update_task_dashboard(self):
        """Update the task dashboard with current task statistics"""
        if not hasattr(self, 'active_tasks_table'):
            return
        
        # Prevent concurrent dashboard updates 
        if getattr(self, '_updating_dashboard', False):
            return
        
        self._updating_dashboard = True
        try:
            return self._do_update_task_dashboard()
        finally:
            self._updating_dashboard = False
    
    def _do_update_task_dashboard(self):
        """Internal method that does the actual dashboard update"""
        import sqlite3
        from datetime import datetime, date
        
        # Check if we should filter by subtree
        subtree_only = hasattr(self, 'subtree_tasks_only') and self.subtree_tasks_only.isChecked()
        focused_root_id = self.tree_widget.get_focused_root() if subtree_only else None
        
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Build WHERE clause for subtree filtering
            subtree_where = ""
            subtree_params = []
            if focused_root_id and focused_root_id != 1:  # If focused on a subtree (not root)
                # Get the focused root note's path
                focused_note = self.db.get_note(focused_root_id)
                if focused_note:
                    focused_path = focused_note['path']
                    # Filter for notes that are descendants of the focused root
                    subtree_where = "AND (n.path LIKE ? OR n.id = ?)"
                    subtree_params = [f"{focused_path}.%", focused_root_id]
            
            # Get active task count
            query = f"SELECT COUNT(*) as count FROM tasks t JOIN notes n ON t.note_id = n.id WHERE t.status = 'active' {subtree_where}"
            cursor = conn.execute(query, subtree_params)
            active_count = cursor.fetchone()['count']
            scope_text = " (subtree)" if subtree_only and focused_root_id != 1 else ""
            self.task_active_label.setText(f"Active Tasks: {active_count}{scope_text}")
            
            # Get tasks completed today count
            today = date.today().isoformat()
            query = f"""
                SELECT COUNT(*) as count 
                FROM tasks t
                JOIN notes n ON t.note_id = n.id
                WHERE t.status = 'complete' 
                AND date(n.modified_at) = ?
                {subtree_where}
            """
            params = [today] + subtree_params
            cursor = conn.execute(query, params)
            completed_today_count = cursor.fetchone()['count']
            self.task_completed_today_label.setText(f"Completed Today: {completed_today_count}{scope_text}")
            
            # Get active tasks with details
            query = f"""
                SELECT n.content, t.start_date, t.due_date, t.priority, t.completed_at, n.id
                FROM notes n
                JOIN tasks t ON n.id = t.note_id
                WHERE t.status = 'active'
                {subtree_where}
            """
            cursor = conn.execute(query, subtree_params)
            raw_tasks = cursor.fetchall()
            
            # Debug: Check for duplicate IDs in raw data
            task_ids = [task['id'] for task in raw_tasks]
            unique_ids = set(task_ids)
            if len(task_ids) != len(unique_ids):
                print(f"DEBUG: Found duplicate task IDs in database query! Total: {len(task_ids)}, Unique: {len(unique_ids)}")
                duplicate_ids = [id for id in task_ids if task_ids.count(id) > 1]
                print(f"DEBUG: Duplicate IDs: {duplicate_ids}")
            
            # Categorize and sort tasks intelligently
            tasks = self.categorize_and_sort_tasks(raw_tasks)
            
            # Debug: Check for duplicates after categorization
            categorized_ids = [task['id'] for task in tasks]
            unique_categorized = set(categorized_ids)
            if len(categorized_ids) != len(unique_categorized):
                print(f"DEBUG: Found duplicate task IDs after categorization! Total: {len(categorized_ids)}, Unique: {len(unique_categorized)}")
                duplicate_categorized = [id for id in categorized_ids if categorized_ids.count(id) > 1]
                print(f"DEBUG: Duplicate IDs after categorization: {duplicate_categorized}")
            
            # Block signals to prevent infinite loops during table updates
            self.active_tasks_table.blockSignals(True)
            
            # Explicitly clear the table first to prevent duplicates
            self.active_tasks_table.clearContents()
            self.active_tasks_table.setRowCount(0)
            self.active_tasks_table.setRowCount(len(tasks))
            
            for row_idx, task in enumerate(tasks):
                # Task content with category label (read-only)
                content = task['content'][:50] + "..." if len(task['content']) > 50 else task['content']
                if not content.strip():
                    content = "(empty task)"
                
                # Add category prefix
                category = task.get('category', 'Misc')
                if category == 'In Progress':
                    content_with_category = f"⏳ {content}"
                elif category == 'Upcoming':
                    content_with_category = f"📅 {content}"
                elif category == 'Future':
                    content_with_category = f"🗓️ {content}"
                else:  # Misc
                    content_with_category = f"📝 {content}"
                
                content_item = QTableWidgetItem(content_with_category)
                content_item.setFlags(content_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Make read-only
                content_item.setData(Qt.ItemDataRole.UserRole, task['id'])  # Store note ID
                # Add visual indication that this is clickable
                content_item.setToolTip(f"Click to jump to this note in the tree\nNote ID: {task['id']}")
                content_item.setForeground(QColor("#0066cc"))  # Blue color to indicate it's clickable
                self.active_tasks_table.setItem(row_idx, 0, content_item)
                
                # Start date (editable)
                start_date = task['start_date']
                if start_date:
                    try:
                        # Parse and format date
                        dt = datetime.fromisoformat(start_date)
                        formatted_date = dt.strftime("%m/%d/%Y %I:%M %p")
                    except:
                        formatted_date = str(start_date)
                else:
                    formatted_date = "-"
                self.active_tasks_table.setItem(row_idx, 1, QTableWidgetItem(formatted_date))
                
                # Due date
                due_date = task['due_date']
                if due_date:
                    try:
                        # Parse and format date
                        dt = datetime.fromisoformat(due_date)
                        formatted_date = dt.strftime("%m/%d/%Y %I:%M %p")
                    except:
                        formatted_date = str(due_date)
                else:
                    formatted_date = "-"
                self.active_tasks_table.setItem(row_idx, 2, QTableWidgetItem(formatted_date))
                
                # Priority
                priority = task['priority'] if task['priority'] is not None else 0
                priority_item = QTableWidgetItem(str(priority))
                # Make priority column sortable as numbers
                priority_item.setData(Qt.ItemDataRole.UserRole, priority)
                self.active_tasks_table.setItem(row_idx, 3, priority_item)
            
            # Re-enable signals after all updates are complete
            self.active_tasks_table.blockSignals(False)
    
    def on_task_table_item_clicked(self, item):
        """Handle clicks on task table items - navigate to the note only for task name column"""
        if not item:
            return
        
        try:
            # Only navigate when clicking on the task name column (column 0)
            # Allow editing for other columns (1: Start Date, 2: Due Date, 3: Priority)
            column = item.column()
            if column != 0:
                return  # Don't navigate for editable columns
            
            # Get the note ID from the first column of the same row
            row = item.row()
            first_column_item = self.active_tasks_table.item(row, 0)
            if not first_column_item:
                return
            
            note_id = first_column_item.data(Qt.ItemDataRole.UserRole)
            if note_id is None:
                return
            
            # Navigate to the note (only from task name column)
            self.find_and_select_note(note_id)
            
            # Give visual feedback - safely get task name
            try:
                task_name = first_column_item.text().replace('...', '')
                self.status_bar.showMessage(f"Jumped to task: {task_name}", 2000)
            except RuntimeError:
                # Item was deleted during update - just show generic message
                self.status_bar.showMessage(f"Jumped to note {note_id}", 2000)
                
        except (RuntimeError, AttributeError) as e:
            # Handle case where table items were deleted during update
            self.status_bar.showMessage("Unable to navigate - table is being updated", 2000)
    
    def update_start_date(self):
        """Update the start date for the current task"""
        if not hasattr(self, 'current_task_id') or not self.current_task_id:
            return
        
        # Prevent loops during programmatic updates
        if hasattr(self, '_updating_dates') and self._updating_dates:
            return
        
        self._updating_dates = True
        
        text = self.detail_start_date.text().strip()
        print(f"Parsing start date: '{text}'")
        parsed_date = parse_natural_date(text) if text else None
        print(f"Parsed result: {parsed_date}")
        
        try:
            self.db.update_task_date(self.current_task_id, 'start_date', parsed_date)
            
            # Refresh the note data from database to get updated dates
            updated_note_data = self.db.get_note(self.current_task_id)
            if updated_note_data:
                # Update the tree item's data
                selected_items = [item for item in self.tree_widget.selectedItems() 
                                 if isinstance(item, EditableTreeItem)]
                if selected_items:
                    selected_items[0].note_data = updated_note_data
                    selected_items[0].update_display()
            
            if parsed_date:
                # Update display with parsed result
                self.detail_start_date.setText(parsed_date.strftime('%m/%d/%Y %I:%M %p'))
                self.status_bar.showMessage(f"Start date set to {parsed_date.strftime('%m/%d/%Y %I:%M %p')}", 2000)
            else:
                if text:  # User entered something but it didn't parse
                    self.status_bar.showMessage(f"Could not parse '{text}' as a date", 3000)
                else:
                    self.status_bar.showMessage("Start date cleared", 2000)
        except Exception as e:
            self.status_bar.showMessage(f"Error updating start date: {str(e)}", 3000)
        finally:
            self._updating_dates = False
    
    def update_due_date(self):
        """Update the due date for the current task"""
        if not hasattr(self, 'current_task_id') or not self.current_task_id:
            return
        
        # Prevent loops during programmatic updates
        if hasattr(self, '_updating_dates') and self._updating_dates:
            return
        
        self._updating_dates = True
        
        text = self.detail_due_date.text().strip()
        print(f"Parsing due date: '{text}'")
        parsed_date = parse_natural_date(text) if text else None
        print(f"Parsed result: {parsed_date}")
        
        try:
            self.db.update_task_date(self.current_task_id, 'due_date', parsed_date)
            
            # Refresh the note data from database to get updated dates
            updated_note_data = self.db.get_note(self.current_task_id)
            if updated_note_data:
                # Update the tree item's data
                selected_items = [item for item in self.tree_widget.selectedItems() 
                                 if isinstance(item, EditableTreeItem)]
                if selected_items:
                    selected_items[0].note_data = updated_note_data
                    selected_items[0].update_display()
            
            if parsed_date:
                # Update display with parsed result
                self.detail_due_date.setText(parsed_date.strftime('%m/%d/%Y %I:%M %p'))
                self.status_bar.showMessage(f"Due date set to {parsed_date.strftime('%m/%d/%Y %I:%M %p')}", 2000)
            else:
                if text:  # User entered something but it didn't parse
                    self.status_bar.showMessage(f"Could not parse '{text}' as a date", 3000)
                else:
                    self.status_bar.showMessage("Due date cleared", 2000)
        except Exception as e:
            self.status_bar.showMessage(f"Error updating due date: {str(e)}", 3000)
        finally:
            self._updating_dates = False
    
    def update_priority(self):
        """Update the priority for the current task"""
        if not hasattr(self, 'current_task_id') or not self.current_task_id:
            return
        
        # Prevent loops during programmatic updates
        if hasattr(self, '_updating_priority') and self._updating_priority:
            return
        
        self._updating_priority = True
        
        priority = self.detail_priority.value()
        
        try:
            # Update in database
            with sqlite3.connect(self.db.db_path) as conn:
                # Ensure task exists
                cursor = conn.execute("SELECT note_id FROM tasks WHERE note_id = ?", (self.current_task_id,))
                if not cursor.fetchone():
                    # Create task if it doesn't exist
                    conn.execute("INSERT INTO tasks (note_id, status) VALUES (?, 'active')", (self.current_task_id,))
                
                # Update the priority
                conn.execute("UPDATE tasks SET priority = ? WHERE note_id = ?", (priority, self.current_task_id))
                
                # Update the note's modified timestamp since metadata changed
                conn.execute("UPDATE notes SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (self.current_task_id,))
                
                conn.commit()
            
            # Auto-commit to git
            if self.db.git_vc:
                self.db.git_vc.commit_changes(f"Update task {self.current_task_id} priority to {priority}")
            
            # Update tree item data
            selected_items = [item for item in self.tree_widget.selectedItems() 
                             if isinstance(item, EditableTreeItem)]
            if selected_items:
                updated_note_data = self.db.get_note(self.current_task_id)
                if updated_note_data:
                    selected_items[0].note_data = updated_note_data
                    selected_items[0].update_display()
            
            # Update task dashboard
            self.update_task_dashboard()
            
            priority_text = "None" if priority == 0 else str(priority)
            self.status_bar.showMessage(f"Priority set to {priority_text}", 2000)
            
        except Exception as e:
            self.status_bar.showMessage(f"Error updating priority: {str(e)}", 3000)
        finally:
            self._updating_priority = False
    
    def on_task_table_item_changed(self, item):
        """Handle edits to task table items"""
        if not item:
            return
        
        row = item.row()
        column = item.column()
        
        # Get the note ID from the first column
        first_column_item = self.active_tasks_table.item(row, 0)
        if not first_column_item:
            return
        
        note_id = first_column_item.data(Qt.ItemDataRole.UserRole)
        if not note_id:
            return
        
        try:
            if column == 1:  # Start date
                text = item.text().strip()
                if text == "-" or not text:
                    parsed_date = None
                else:
                    parsed_date = parse_natural_date(text)
                    if parsed_date is None:
                        # Reset to original value if parsing failed
                        self.status_bar.showMessage(f"Could not parse date: '{text}'", 3000)
                        self.update_task_dashboard()  # Refresh to reset value
                        self._dashboard_refreshed = True  # Prevent duplicate timer-based refresh
                        return
                
                # Update in database
                self.db.update_task_date(note_id, 'start_date', parsed_date)
                self.status_bar.showMessage("Start date updated", 2000)
                
            elif column == 2:  # Due date
                text = item.text().strip()
                if text == "-" or not text:
                    parsed_date = None
                else:
                    parsed_date = parse_natural_date(text)
                    if parsed_date is None:
                        # Reset to original value if parsing failed
                        self.status_bar.showMessage(f"Could not parse date: '{text}'", 3000)
                        self.update_task_dashboard()  # Refresh to reset value
                        self._dashboard_refreshed = True  # Prevent duplicate timer-based refresh
                        return
                
                # Update in database
                self.db.update_task_date(note_id, 'due_date', parsed_date)
                self.status_bar.showMessage("Due date updated", 2000)
                
            elif column == 3:  # Priority
                try:
                    priority = int(item.text())
                    if priority < 0 or priority > 10:
                        raise ValueError("Priority must be 0-10")
                        
                    # Update in database
                    with sqlite3.connect(self.db.db_path) as conn:
                        conn.execute("UPDATE tasks SET priority = ? WHERE note_id = ?", (priority, note_id))
                        conn.execute("UPDATE notes SET modified_at = CURRENT_TIMESTAMP WHERE id = ?", (note_id,))
                        conn.commit()
                    
                    # Auto-commit to git
                    if self.db.git_vc:
                        self.db.git_vc.commit_changes(f"Update task {note_id} priority to {priority}")
                    
                    self.status_bar.showMessage(f"Priority updated to {priority}", 2000)
                    
                except ValueError:
                    self.status_bar.showMessage("Priority must be a number 0-10", 3000)
                    self.update_task_dashboard()  # Refresh to reset value
                    self._dashboard_refreshed = True  # Prevent duplicate timer-based refresh
                    return
            
            # Update details panel if this task is currently selected
            if hasattr(self, 'current_task_id') and self.current_task_id == note_id:
                self.update_details_panel()
            
            # Refresh dashboard to show updated values (if not already refreshed)
            if not getattr(self, '_dashboard_refreshed', False):
                QTimer.singleShot(100, self.refresh_dashboard_after_edit)
            else:
                self._dashboard_refreshed = False  # Reset flag for next time
            
        except Exception as e:
            self.status_bar.showMessage(f"Error updating task: {str(e)}", 3000)
            self.update_task_dashboard()  # Refresh to reset value
            self._dashboard_refreshed = True  # Prevent duplicate timer-based refresh
    
    def refresh_dashboard_after_edit(self):
        """Refresh dashboard with signal blocking to prevent loops"""
        self.active_tasks_table.blockSignals(True)
        self.update_task_dashboard()
        self.active_tasks_table.blockSignals(False)
    
    def categorize_and_sort_tasks(self, raw_tasks):
        """Categorize tasks and apply smart sorting"""
        from datetime import datetime, timedelta
        
        now = datetime.now()
        week_from_now = now + timedelta(days=7)
        
        # Categorize tasks
        in_progress = []
        upcoming = []
        misc = []
        
        for task in raw_tasks:
            start_date = task['start_date']
            
            if start_date:
                try:
                    start_dt = datetime.fromisoformat(start_date)
                    if start_dt <= now:
                        # Task is in progress (start date has passed)
                        task_dict = dict(task)
                        task_dict['category'] = 'In Progress'
                        in_progress.append(task_dict)
                    elif start_dt <= week_from_now:
                        # Task is upcoming (starts within a week)
                        task_dict = dict(task)
                        task_dict['category'] = 'Upcoming'
                        upcoming.append(task_dict)
                    else:
                        # Task starts more than a week away
                        task_dict = dict(task)
                        task_dict['category'] = 'Future'
                        misc.append(task_dict)
                except:
                    # Invalid date format, treat as misc
                    task_dict = dict(task)
                    task_dict['category'] = 'Misc'
                    misc.append(task_dict)
            else:
                # No start date
                task_dict = dict(task)
                task_dict['category'] = 'Misc'
                misc.append(task_dict)
        
        # Smart sorting function
        def smart_sort_key(task):
            priority = task['priority'] or 0
            due_date = task['due_date']
            content = task['content']
            
            # Priority 0 (None) should be at the bottom, then ascending priority (lower numbers = higher priority)
            priority_sort = (1 if priority == 0 else 0, priority if priority != 0 else 999)
            
            # Due date sorting (None dates go to end)
            if due_date:
                try:
                    due_dt = datetime.fromisoformat(due_date)
                    due_sort = (0, due_dt)
                except:
                    due_sort = (1, datetime.max)
            else:
                due_sort = (1, datetime.max)
            
            # Content for tie-breaking
            content_sort = content.lower()
            
            return (priority_sort, due_sort, content_sort)
        
        # Sort each category
        in_progress.sort(key=smart_sort_key)
        upcoming.sort(key=smart_sort_key)
        misc.sort(key=smart_sort_key)
        
        # Combine categories in order: in progress, upcoming, misc
        return in_progress + upcoming + misc
    
    def restore_smart_sort(self):
        """Restore intelligent categorized sorting and refresh the dashboard"""
        print("Smart sort button clicked!")  # Debug
        
        # Get current table contents for debugging
        before_items = []
        for row in range(self.active_tasks_table.rowCount()):
            item = self.active_tasks_table.item(row, 0)
            if item:
                before_items.append(item.text())
        print(f"Before smart sort: {before_items}")
        
        # Disable table sorting to allow custom sorting
        self.active_tasks_table.setSortingEnabled(False)
        
        # Clear any existing sort indicator and reset sort state
        header = self.active_tasks_table.horizontalHeader()
        header.setSortIndicatorShown(False)
        
        # Clear the sort indicator completely - this is key!
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        
        # Refresh the dashboard with smart sorting
        self.update_task_dashboard()
        
        # Get table contents after refresh for debugging
        after_items = []
        for row in range(self.active_tasks_table.rowCount()):
            item = self.active_tasks_table.item(row, 0)
            if item:
                after_items.append(item.text())
        print(f"After smart sort: {after_items}")
        
        # Re-enable table sorting for future manual sorting, but don't show indicator yet
        self.active_tasks_table.setSortingEnabled(True)
        # Don't restore setSortIndicatorShown(True) - let user click columns to sort again
        
        if before_items != after_items:
            self.status_bar.showMessage("Restored smart categorized sorting", 2000)
        else:
            self.status_bar.showMessage("Smart sort: No change detected", 2000)
    
    def increase_font_size(self):
        """Increase font size by 1 point"""
        current_font = self.tree_widget.font()
        new_size = min(current_font.pointSize() + 1, 24)  # Max size 24pt
        self.apply_font_size(new_size)
    
    def decrease_font_size(self):
        """Decrease font size by 1 point"""
        current_font = self.tree_widget.font()
        new_size = max(current_font.pointSize() - 1, 8)  # Min size 8pt
        self.apply_font_size(new_size)
    
    def reset_font_size(self):
        """Reset font size to default"""
        self.apply_font_size(self.default_font_size)
    
    def apply_font_size(self, size):
        """Apply font size to all UI elements and save setting"""
        # Apply to tree widget
        tree_font = self.tree_widget.font()
        tree_font.setPointSize(size)
        self.tree_widget.setFont(tree_font)
        
        # Apply to side panels
        panel_font = self.details_panel.font()
        panel_font.setPointSize(size)
        self.details_panel.setFont(panel_font)
        self.task_dashboard.setFont(panel_font)
        
        # Save font size setting
        self.save_font_size(size)
    
    def save_font_size(self, size):
        """Save font size to a settings file"""
        try:
            import json
            
            # Load existing settings
            settings = {}
            try:
                with open("settings.json", "r") as f:
                    settings = json.load(f)
            except Exception:
                pass
            
            # Update font size
            settings["font_size"] = size
            
            # Save settings
            with open("settings.json", "w") as f:
                json.dump(settings, f)
        except Exception as e:
            print(f"Could not save font size: {e}")
    
    def load_font_size(self):
        """Load font size from settings file"""
        try:
            import json
            with open("settings.json", "r") as f:
                settings = json.load(f)
                font_size = settings.get("font_size", self.default_font_size)
                self.apply_font_size(font_size)
        except Exception:
            # No settings file or error reading it - use default
            pass
    
    def undo(self):
        """Undo last change using git"""
        if self.db.git_vc and self.db.git_vc.undo():
            # Reload the tree after undo
            self.tree_widget.load_tree()
            self.status_bar.showMessage("Undid last change", 3000)
        else:
            self.status_bar.showMessage("Cannot undo - no previous version available", 3000)
    
    def redo(self):
        """Redo last undone change using git"""
        if self.db.git_vc and self.db.git_vc.redo():
            # Reload the tree after redo
            self.tree_widget.load_tree()
            self.status_bar.showMessage("Redid last change", 3000)
        else:
            self.status_bar.showMessage("Cannot redo - no forward version available", 3000)
    
    def show_git_history(self):
        """Show git commit history"""
        if not self.db.git_vc:
            return
            
        history = self.db.git_vc.get_history(20)
        if not history:
            QMessageBox.information(self, "History", "No version history available")
            return
        
        # Create history dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Version History")
        dialog.setModal(True)
        dialog.resize(600, 400)
        
        layout = QVBoxLayout(dialog)
        
        # Create history list
        history_list = QListWidget()
        for commit in history:
            item_text = f"{commit['date'].strftime('%Y-%m-%d %H:%M:%S')} - {commit['message']}"
            history_list.addItem(item_text)
        
        layout.addWidget(QLabel("Recent changes (newest first):"))
        layout.addWidget(history_list)
        
        # Add close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def closeEvent(self, event):
        """Handle application closing"""
        if self.tree_widget.editing_item:
            self.tree_widget.finish_editing()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()