/* Instructions hidden in files or command output that try to steer an agent. */

rule ignore_previous_instructions
{
    strings:
        $a = /(ignore|disregard|forget) +(all +|any +|the +)?(previous|prior|above|earlier|your) +(instructions|prompts|directions|rules)/ nocase
    condition:
        $a
}

rule role_override
{
    strings:
        $you_are_now = /you are now +(a|an|in|the) / nocase
        $tags = /<\/?(system|system_prompt|instructions)>/ nocase
        $new_prompt = /new +system +(prompt|instructions)/ nocase
    condition:
        any of them
}

rule secret_exfiltration
{
    strings:
        $a = /(print|send|post|upload|reveal|exfiltrate|leak|output) +[^\n]{0,40}(api[ _-]?key|access token|secret|credential|environment variables|env vars)/ nocase
    condition:
        $a
}
