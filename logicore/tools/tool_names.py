"""
Centralized tool name constants.

All hardcoded tool name strings across the codebase should reference these
constants instead of raw strings. This prevents typos, enables IDE navigation,
and makes renames a single-file change.
"""


class ToolName:
    # Filesystem
    READ_FILE = "read_file"
    CREATE_FILE = "create_file"
    EDIT_FILE = "edit_file"
    DELETE_FILE = "delete_file"
    LIST_FILES = "list_files"
    SEARCH_FILES = "search_files"
    FAST_GREP = "fast_grep"

    # Execution
    EXECUTE_COMMAND = "execute_command"
    CODE_EXECUTE = "code_execute"
    BASH = "bash"

    # Process management
    LIST_PROCESSES = "list_processes"
    KILL_PROCESS = "kill_process"
    GET_PROCESS_INFO = "get_process_info"
    GET_PROCESS_OUTPUT = "get_process_output"
    TAIL_PROCESS_OUTPUT = "tail_process_output"
    WATCH_PROCESS = "watch_process"

    # Web
    WEB_SEARCH = "web_search"
    IMAGE_SEARCH = "image_search"
    URL_FETCH = "url_fetch"

    # Git
    GIT_COMMAND = "git_command"

    # Document
    READ_DOCUMENT = "read_document"
    CONVERT_DOCUMENT = "convert_document"

    # Media
    MEDIA_SEARCH = "media_search"

    # Cron
    ADD_CRON_JOB = "add_cron_job"
    LIST_CRON_JOBS = "list_cron_jobs"
    REMOVE_CRON_JOB = "remove_cron_job"
    GET_CRONS = "get_crons"

    # SmartAgent specific
    DATETIME = "datetime"
    NOTES = "notes"
    THINK = "think"

    # Task management
    TASK_CREATE = "task_create"
    TASK_GET = "task_get"
    TASK_UPDATE = "task_update"
    TASK_LIST = "task_list"
    TASK_NEXT = "task_next"

    # Plan
    ENTER_PLAN_MODE = "enter_plan_mode"
    SUBMIT_PLAN = "submit_plan"
    EXIT_PLAN_MODE = "exit_plan_mode"
    UPDATE_PLAN_PROGRESS = "update_plan_progress"
    VIEW_PLAN = "view_plan"

    # Skill management
    LOAD_SKILL = "load_skill"
