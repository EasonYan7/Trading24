# Trading24 Product Design Audit

Date: 2026-07-02
Target: `http://localhost:8501/`
Destination: local folder
Mode: combined UX and accessibility audit

## Audit Scope

This audit covers the current Trading24 local dashboard flow: overview, search/add discovery, analysis page, decision-history operations, watchlist management, dark mode, and AI assistant. Evidence comes only from screenshots and DOM observations captured during this audit run.

## Step List

1. Current narrow overview - Needs work
   - Screenshot: `screenshots/01-current-page.png`
   - The page loses right-side content at the current narrow width. Search and right-side controls are not discoverable.

2. Desktop overview - Partially healthy
   - Screenshot: `screenshots/02-overview-desktop.png`
   - The main structure is clear: watchlist on the left, status and alerts on the right. Cards now show Chinese company names with smaller codes, which is easier to scan.

3. Search with unknown ticker - Partially healthy
   - Screenshot: `screenshots/03-search-add-candidate.png` (NOTE: this file is a duplicate of `02-overview-desktop.png` and does not show the search result; re-capture pending)
   - Typing `TSLA` shows `Add: TSLA`, so the entry direction is right. The add action was not clicked because it may start a real analysis run and incur cost.
   - Verified live on 2026-07-02: entering `TSLA` in the desktop search box does surface an `Add: TSLA` candidate. Conclusion holds; only the screenshot is misassigned.

4. Analysis page - Partially healthy
   - Screenshot: `screenshots/04-analysis-page.png`
   - The page contains selector, run status, price summary, chart, decision history, and agent reports. It is complete, but dense.

5. Decision operation mode - Mostly healthy
   - Screenshot: `screenshots/06-operation-mode-history-visible.png` (was `05-analysis-operation-mode.png`, which only captured the Agent Pipeline sidebar and chart, not the operation-mode controls)
   - The `操作` button collapses report content and exposes selection plus a disabled delete button. Verified live on 2026-07-02: `取消` is enabled, `删除` starts disabled, and each history row gains an unchecked checkbox.

6. Decision selected state - Mostly healthy
   - Screenshot: `screenshots/06-analysis-operation-selected.png` (NOTE: this file is a duplicate of `05-analysis-operation-mode.png` and does not show the selected state; re-capture pending)
   - Selecting the decision enables `删除`. The final delete confirmation was not tested to avoid changing local data.
   - Verified live on 2026-07-02: checking the `2026-06-26` row highlights it green and switches `删除` from disabled to enabled. Conclusion holds; only the screenshot is misassigned.

7. Watchlist management - Partially healthy
   - Screenshot: `screenshots/07-watchlist-management.png`
   - Running time, drag order, edit/delete, and add ticker are present. The layout is usable but has duplicated management areas.

8. Watchlist edit mode - Needs work
   - Screenshot: `screenshots/08-watchlist-edit-mode-retake.png`
   - Editing works for name and thresholds. However, edit and add forms appear together, which splits the user’s attention.

9. Dark mode - Needs work
   - Screenshot: `screenshots/09-watchlist-dark-mode-retake.png`
   - The overall dark theme renders after a delay, but disabled fields and numeric steppers have weak visual states and inconsistent contrast.

10. AI assistant open - Partially healthy
    - Screenshot: `screenshots/10-ai-assistant-open.png`
    - The assistant behaves like a floating support/chat panel. It has prompts and a message box, but lacks an obvious multi-day record attachment flow or visible history drawer.

## Strengths

- The product now has a cleaner three-page IA: `总览`, `分析`, `关注`.
- Overview follows the intended left watchlist plus right status/alerts model on desktop.
- Search can identify a ticker outside the current watchlist and present an add candidate.
- Analysis page keeps the selected ticker, price, chart, decision history, and reports in one working context.
- Decision delete mode is safer than before: records collapse, selection is explicit, and delete starts disabled.
- Dark mode is much closer to the intended Robinhood-like professional theme than earlier versions.

## UX Risks

1. **Responsive shell is still fragile.**
   - On the current narrow viewport, navigation and page content are clipped. Search is not visible, so adding a company becomes undiscoverable.

2. **Analysis page is complete but crowded.**
   - The selector, completion banner, price card, chart, decision history, and report navigation compete for attention. The primary user goal should be clearer: choose ticker, inspect decision, then expand report details.

3. **Watchlist management has duplicated surfaces.**
   - The drag list and the edit/delete list represent the same tickers in two different places. This creates a “which one should I use?” moment.

4. **Editing and adding appear at the same time.**
   - When editing a ticker, the add form remains below it. A drawer/modal or a single focused edit panel would reduce confusion.

5. **AI assistant is useful but not yet connected to the record workflow.**
   - The panel does not clearly expose “select multiple days, attach them, then ask AI” or “view prior chats.”

## Accessibility Risks

1. **Hidden checkbox implementation needs attention.**
   - In decision operation mode, the real checkbox has a 0x0 rectangle while the visible label is clickable. This may be fragile for keyboard and assistive-technology users.

2. **Icon-only controls need accessible names.**
   - The AI panel exposes unlabeled icon buttons in the DOM. These should have labels such as `查看历史`, `新对话`, or `关闭`.

3. **Dark mode contrast is inconsistent.**
   - Disabled inputs and numeric stepper controls are visible but not clearly differentiated. Some controls rely mostly on border brightness.

4. **Drag ordering inside an iframe may be hard to operate.**
   - The draggable list is exposed as buttons inside an iframe. Keyboard drag, screen reader instructions, and stable drop feedback still need verification.

## Recommendations

1. Fix the responsive app shell first: at narrow widths, stack the top nav, keep search reachable, and avoid horizontal clipping.
2. Make search the main add-company path: show suggestions, show `添加`, then confirm whether to start a real analysis run before spending LLM cost.
3. Simplify analysis into one vertical task flow: ticker selector, price/decision summary, chart, decision history, expandable report modules.
4. Keep decision operation mode visually identical to the normal row, only adding a left checkbox and a top-right action bar.
5. Replace duplicate watchlist management with a single row pattern: drag handle on the left, ticker info in the middle, edit/delete icon buttons on the right.
6. Move edit/add into focused panels so only one task is active at a time.
7. Add AI assistant record tools: select history records, attach selected records, view chat history, start new chat.
8. Audit dark theme tokens for inputs, disabled fields, steppers, borders, and secondary text.

## Evidence Limits

- The final delete confirmation was not clicked because it could modify real local data.
- The `Add: TSLA` search result was not clicked because it may start a real analysis run and incur model cost.
- Screenshot evidence cannot prove full WCAG compliance; keyboard navigation and screen reader output need a separate pass.
- Screenshots for steps 3, 5, and 6 were misassigned in the original run (03 duplicated 02; 06-selected duplicated 05; 05 captured the wrong region). All three conclusions were re-verified live on 2026-07-02; step 5 now points to `06-operation-mode-history-visible.png`, and fresh captures for steps 3 and 6 are still pending.
