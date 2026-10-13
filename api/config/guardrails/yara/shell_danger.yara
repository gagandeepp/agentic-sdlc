/* Bash commands an agent must never run. Rule names appear in guardrail.blocked events. */

rule env_dump
{
    strings:
        $proc_environ = /\/proc\/[^ ]*\/environ/
        $dump = /(^|[;&|(] *)(env|printenv)( *$| *[|>;&])/
    condition:
        any of them
}

rule sudo
{
    strings:
        $a = /(^|[;&|(] *)sudo /
    condition:
        $a
}

rule pipe_to_shell
{
    strings:
        $a = /(curl|wget)[^|;&]*\| *(sudo +)?(ba|z|da)?sh\b/
    condition:
        $a
}

rule rm_root_or_home
{
    strings:
        $a = /\brm +-[A-Za-z]*[rR][A-Za-z]* +(\/|~)\/?\*?( |$)/
    condition:
        $a
}

rule git_push
{
    strings:
        $a = /\bgit +push\b/
    condition:
        $a
}

rule git_remote_or_global_config
{
    strings:
        $remote = /\bgit +remote +(add|set-url)\b/
        $global = /\bgit +config +--global\b/
    condition:
        any of them
}

rule workflow_files
{
    strings:
        $a = /\.github\/workflows/
    condition:
        $a
}
