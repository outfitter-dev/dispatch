# Reduce Claude stream JSON immediately. Never retain message/model/tool content.
(env.DISPATCH_CLAUDE_PREFLIGHT_NONCE // "") as $expected_preflight_nonce
| if .subtype == "hook_started" or .subtype == "hook_response" then
  {
    sequence: input_line_number,
    type,
    subtype,
    session_id,
    hook_id,
    hook_event,
    hook_name,
    exit_code,
    outcome,
    dispatch_preflight: (
      $expected_preflight_nonce != ""
      and (((.stdout // "") | fromjson? // {})._dispatch_preflight.nonce // "")
        == $expected_preflight_nonce
    ),
    blocking_decision: (
      ((.stdout // "") | fromjson? // {})
      | .decision == "block"
    )
  }
elif .type == "assistant" then
  {sequence: input_line_number, type, session_id}
elif .type == "result" then
  {sequence: input_line_number, type, subtype, session_id, is_error}
elif .type == "system" and .subtype == "init" then
  {sequence: input_line_number, type, subtype, session_id}
else
  empty
end
| with_entries(select(.value != null and .value != false))
