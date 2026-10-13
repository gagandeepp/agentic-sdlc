/* Credentials that must never be published. Rule names appear in guardrail.blocked events. */

rule anthropic_api_key
{
    strings:
        $a = /sk-ant-[A-Za-z0-9_-]{16,}/
    condition:
        $a
}

rule github_token
{
    strings:
        $classic = /gh[pousr]_[A-Za-z0-9]{30,}/
        $fine_grained = /github_pat_[A-Za-z0-9_]{30,}/
    condition:
        any of them
}

rule aws_access_key_id
{
    strings:
        $a = /(AKIA|ASIA)[A-Z0-9]{16}/
    condition:
        $a
}
