# ask_user

Lets the agent reach *you* — push a notification, or ask a question and leave the answer somewhere
an automation can pick it up.

Useful when the agent is invoked from an automation rather than from a chat window, where there is
nobody watching the conversation.

## Function

Requires an `input_text` helper. Settings → Devices & Services → Helpers → Create helper → Text,
named `Lumo question` (gives you `input_text.lumo_question`).

```yaml
- spec:
    name: ask_user
    description: >-
      Send a message to the user's phone or the Home Assistant UI. Use when you need to tell
      them something they are not present to hear, or to ask a question they can answer later.
      Do not use this to reply in a normal conversation — just answer directly.
    parameters:
      type: object
      properties:
        message:
          type: string
          description: The message or question to send. One or two sentences.
        title:
          type: string
          description: Short title for the notification. Defaults to "Lumo".
        urgent:
          type: boolean
          description: >-
            True to also push to the mobile app, false for a UI notification only.
      required: [message]
  function:
    type: script
    sequence:
      - action: persistent_notification.create
        data:
          title: "{{ title | default('Lumo') }}"
          message: "{{ message }}"
      - action: input_text.set_value
        target:
          entity_id: input_text.lumo_question
        data:
          value: "{{ message[:255] }}"
      - if:
          - condition: template
            value_template: "{{ urgent | default(false) }}"
        then:
          - action: notify.notify
            data:
              title: "{{ title | default('Lumo') }}"
              message: "{{ message }}"
      - variables:
          _function_result: "Message delivered to the user"
```

## Try it

From an automation:

```yaml
      - action: conversation.process
        data:
          agent_id: conversation.lumo_conversation
          text: >-
            Check whether any window or door is still open. If so, notify me urgently
            with a list. If everything is shut, do nothing at all.
```

## Notes

- The "if everything is shut, do nothing at all" phrasing matters. Without an explicit no-op branch
  models tend to call the tool anyway just to report success.
- `input_text` caps values at 255 characters, hence the slice.
- `notify.notify` targets whatever notifier you have configured. Swap in
  `notify.mobile_app_<your_phone>` to be specific.
