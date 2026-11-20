# Selection checklist widget — implementation pattern

Referenced from step 7 of [`SKILL.md`](../SKILL.md). Build the widget body in JS from
data arrays so rows stay consistent; the group precedence and helper-button behaviour
are defined in step 7.

```js
// data arrays: REC (ticketed, pre-checked), SYS (system defaults),
// SUP (superseded or not live), LEG (legacy) — each row = [eventName, volume, ticketOrNull]
function row(name, vol, ticket, checked){
  // <label><input type=checkbox data-name=...> name · volume · [ticket badge]</label>
}
// "Select ticketed only" → check rows whose name is in REC; "Clear all" → uncheck all
// live count updates on every change
document.getElementById("submit").addEventListener("click", () => {
  const picked = boxes().filter(b => b.checked).map(b => b.dataset.name);
  if (!picked.length) { alert("Pick at least one event to un-hide."); return; }
  sendPrompt("Un-hide exactly these hidden Mixpanel events (validate "
    + "against the candidate table first):\n" + picked.map(n => "- " + n).join("\n"));
});
```
