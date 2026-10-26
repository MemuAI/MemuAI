---
title: Team
summary: Models with separate roles, a communication manager and a shared result in regular chats or Work.
---

# Team

Open **Team** beside Inbox and AI skills. Create a reusable group of models for development, research, creative projects or other work.

**Customize sidebar** controls Work, MemuCode, planning, inbox, Team, AI skills, modules, pinned chats, folders and agents. Restore any hidden section or choose “Show all”. Individual module shortcuts share their settings with the Modules page; data and module enabled states are preserved.

## Set up a team

Choose New feature, Research or Creative studio, or select **Create team**. Enter a name and purpose, choose the manager model and assign members. Each member has a name, model, role and optional instructions. The same model can fill several roles.

The manager is a separate role. It talks to you, receives personal instructions and relevant profile memory, and curates the context sent to members. Members do not directly receive personal memory or the conversation history. The manager selects a lead for each task; you can designate a preferred lead in the editor.

## Chat and Work

Choose **Chat** for discussion, research and creating materials. Choose **Work** to operate in a project folder. The conversation is saved in the selected mode’s normal history, including its team activity card. New ordinary chats do not inherit the team.

The lead assigns independent work with explicit responsibilities, collects reports and verifies the outcome. Members can exchange messages; questions pass through the lead and manager. When context is insufficient, the manager shows a clarification form in the chat. The manager presents the final response.

Members share MemuAI’s tools: Work and coding tools, enabled web search, skills, integrations, MCP, modules, Canvas and media generation when the required connections are configured. Existing access settings remain effective. Scheduling requests create proposals that you add manually.

The editor lets you delegate CU and DFY approval review to the manager. The lead evaluates the concrete action, then the manager approves only with high confidence and explicit authorization in the conversation, rejects actions outside scope, or leaves the decision to you. Payments require an explicitly authorized purpose and amount or spending limit. Saved memory and teammate claims do not grant permission. Failed checks or exhausted model budgets fall back to manual confirmation. You approve the initial start of computer control. Passwords, codes and payment details stay in secure forms and are not shared with the team. Disable delegated approvals to keep every such decision manual.

## Activity and stopping

Click the team card in a response to see members, states, tasks, context handoffs and tool activity. Model calls and tokens are shown too. Estimates are identified when providers do not report usage. Hidden reasoning and personal memory are not stored in the activity log.

**Stop** cancels the whole run, including members and pending tools. Switching profiles also stops a run. Interrupted work does not automatically resume after an application restart: send a new message explaining how to continue. File changes already applied remain; use the project’s normal change history to inspect or undo them.

## Spending controls

Expand **Spending and parallel work** in the editor. Limits apply to team model calls, concurrent requests, rounds per member and response length. Each actor’s context is bounded too. These are limits per user message, not a provider currency budget. Manager calls, verification and approval review also count. Internal media generation, CU and DFY calls are billed separately under those tools’ settings. Work may finish before reaching the limits.

Teams belong to their profile. **Show in sidebar** hides the shortcut; reopen the section from the help menu at the top right. Deleting a team keeps its conversations.


You can leave the member list empty. After your request, the manager recruits 2–5 roles from available text models. The manager explains its understanding and proposed plan before workers start. Send clarifications to revise the plan, then approve it to begin. Worker assignments run before the independent reviewer. Reviewers can request repairs with the lead’s approval. A failed page or worker is reported as evidence rather than automatically aborting the entire team.

Select a member in the activity view to see their actions and tasks, or return to the whole team. Completed tasks include their reports. Hidden reasoning and personal memory are not displayed.

New teams default to 1200 model calls, 300 rounds per member and 3 concurrent model calls. There is no separate run-duration limit, but hours of uninterrupted execution depend on the app remaining open, providers, network and tool availability. This is not a guarantee of 12 continuous hours. Existing saved budgets remain unchanged. Use **Customize sidebar** to restore a hidden Team button.
