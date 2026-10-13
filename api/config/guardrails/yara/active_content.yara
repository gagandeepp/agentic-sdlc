/* Active content in published markdown. GitHub strips most of it; this is defense in depth. */

rule script_tag
{
    strings:
        $open = /<script[^>]*>/ nocase
        $close = /<\/script>/ nocase
    condition:
        any of them
}

rule javascript_url
{
    strings:
        $a = /javascript:/ nocase
    condition:
        $a
}
