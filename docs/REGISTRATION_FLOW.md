# Registration flow

## Team

1. A member clicks **Register a Team** in `#registrations`.
2. The bot opens an ephemeral team-name modal and then a guided view with five
   native server-member selectors.
3. On submit, the bot validates current membership, non-bot status, exactly five
   distinct IDs, the team deadline, capacity, and active ownership. The entire
   team plus five pending confirmations is inserted atomically.
4. Each selected member receives a private DM with **Approve** and **Decline**.
5. All five approvals transition the team to `confirmed`, remove those users from
   the substitute pool atomically, notify the registrar, and refresh the panel.
6. A decline, failed DM delivery, or expired deadline leaves the team unconfirmed,
   deactivates its pending membership, privately informs the registrar, and keeps
   the audit history.

## Substitute pool

Clicking **Join Substitute Pool** validates the current guild member, non-bot
status, deadline, absence from a confirmed team, and absence from the active pool.
The insert is idempotent. **Leave Substitute Pool** is provided privately to the
registrant. Confirming a team automatically records and performs any active pool
removal for its five members.

## Withdrawal

The public panel says: “If your team needs to withdraw, please DM Lynx.” Lynx,
the configured organizer account, or a member with Manage Server can use the
organizer-only `/tourney-withdraw <team>` command. It marks the team withdrawn,
deactivates active memberships, frees the slot, makes members eligible for the
substitute pool unless they are on another confirmed team, notifies affected
members and the registrar, and writes audit events.
