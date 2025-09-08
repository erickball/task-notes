# Task Notes

A hierarchical note-taking and task management application built with PyQt6, designed for managing large collections of structured notes with advanced task tracking capabilities.

## Features

### 📝 **Hierarchical Notes**
- Unlimited depth note structure similar to Workflowy
- Inline editing with click-to-edit functionality
- Multi-selection support for bulk operations
- Drag-and-drop reordering with intelligent positioning
- Cut/copy/paste functionality for notes and subtrees

### 🎯 **Task Management**
- Convert any note to a task with Ctrl+Space
- Task status: Active → Complete → No Task (cycles through)
- Priority levels (0-10) with visual indicators
- Natural language date parsing ("tomorrow 2pm", "next friday")
- Start dates and due dates with timezone support
- Task dashboard with filtering and sorting

### ⚡ **Performance & Navigation**
- Subtree focus system for large note collections (thousands of notes)
- Breadcrumb navigation with clickable path elements
- Configurable tree depth limits (5, 10, 15, 20, unlimited)
- Keyboard shortcuts for all operations
- Virtual tree rendering for optimal performance

### 💾 **Data Management**
- SQLite database with materialized path pattern
- Git-based version control with undo/redo (requires pygit2)
- Save/Save As/Open database functionality
- Recent files menu
- Automatic change tracking and commits

### 🎨 **User Interface**
- Resizable panels with persistent layout
- Font size control with system-wide application
- Editable task dashboard table
- Context menus for all operations
- Subtree-only task filtering
- Path rebuild functionality for consistency

## Installation

### Prerequisites
- Python 3.8 or higher
- PyQt6
- python-dateutil (for natural language date parsing)
- pygit2 (optional, for git version control)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/erickball/task-notes.git
   cd task-notes
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python main.py
   ```

## Usage

### Basic Operations
- **New Note**: Ctrl+Enter (creates sibling after current note)
- **Edit Note**: Click on note or press Enter
- **Navigation**: Arrow keys, Tab/Shift+Tab for indentation
- **Task Toggle**: Ctrl+Space (cycles: No Task → Active → Complete → No Task)
- **Delete**: Delete key (with confirmation for notes with children)

### Advanced Features
- **Focus on Subtree**: Right-click → "Focus on [note]" or use breadcrumbs
- **Multi-select**: Ctrl+click or Shift+click for bulk operations
- **Drag & Drop**: 
  - Drop ON item = make child
  - Drop ABOVE/BELOW item = make sibling before/after
- **Cut/Copy/Paste**: Ctrl+X/C/V for moving note subtrees
- **Undo/Redo**: Ctrl+Z/Y (requires git version control)

### Task Management
- **Task Dashboard**: Shows active tasks with editable dates and priorities
- **Natural Language Dates**: "today 2pm", "tomorrow", "next friday", "in 3 days"
- **Priority Levels**: 0 (None) to 10 (Highest), affects sorting
- **Filtering**: Toggle between all tasks or current subtree only

### Database Operations
- **New Database**: Ctrl+N
- **Open Database**: Ctrl+O 
- **Save As**: Ctrl+Shift+S
- **Recent Files**: File menu shows last 10 opened databases

## Architecture

### Core Components
- **main.py**: PyQt6 GUI application with tree widget and panels
- **database.py**: SQLite database manager with git integration
- **requirements.txt**: Python dependencies

### Database Schema
- **notes**: Hierarchical structure with materialized paths
- **tasks**: Task metadata linked to notes
- **Git integration**: Automatic versioning of all changes

### Key Design Patterns
- Materialized path for efficient tree operations
- Signal/slot architecture with loop prevention
- Lazy loading and virtual rendering for performance
- Event filtering for complex keyboard interactions

## Keyboard Shortcuts

| Action | Shortcut | Context |
|--------|----------|---------|
| New Note | Ctrl+Enter | Any |
| Edit Note | Enter | Selection mode |
| Finish Editing | Escape | Edit mode |
| Toggle Task | Ctrl+Space | Any |
| Indent | Tab | Any |
| Outdent | Shift+Tab | Any |
| Navigate | Arrow Keys | Selection mode |
| Multi-select | Ctrl+Click | Any |
| Extend Selection | Shift+Click | Any |
| Cut/Copy/Paste | Ctrl+X/C/V | Any |
| Undo/Redo | Ctrl+Z/Y | Any |
| Delete | Delete | Selection mode |
| Focus Up | Alt+Up | Any |
| Focus Root | Alt+Home | Any |
| New Database | Ctrl+N | Any |
| Open Database | Ctrl+O | Any |
| Save Database As | Ctrl+Shift+S | Any |
| Font Size +/- | Ctrl+=/- | Any |
| Font Size Reset | Ctrl+0 | Any |

## Contributing

This application was developed collaboratively with Claude Code. Contributions are welcome!

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly with various note structures
5. Submit a pull request

### Areas for Enhancement
- Export functionality (Markdown, HTML, JSON)
- Import from other note-taking formats
- Plugin system for extensibility
- Themes and customization options
- Mobile/web interface
- Collaboration features

## License

MIT License - see LICENSE file for details.

## Acknowledgments

Developed with assistance from Claude Code (Anthropic) for hierarchical note management and task tracking workflows.