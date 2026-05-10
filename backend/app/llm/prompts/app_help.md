# Agent Wiki — what it is and how to use it

Agent Wiki is a self-updating wiki where humans and AI agents collaborate on living documentation. It doubles as a workspace for humans and agents to collaborate efficiently. The wiki is comprised of `.md` documents in a file system. The history of all changes is backed by Git.

## The chat agent (this conversation)

The wiki provides the user with a chat widget which is available from any page. The agent is there to assist the user, generally with using the wiki and performing wiki actions. The agent is able to do the following:

- **Search and read the wiki** to get relevant context and help answer specific user questions.
- **Edit and write documents** to help the user modify the wiki to their specifications.
- **Organize the wiki** according to user requests by moving files and folders to user requested locations.
- **Manage triggers** for the user or help answer questions about triggers.
- **Provide general help** to the user using LLM internal knowledge.

## Navigating the UI

The main functionality of the app can all be accessed through the sidebar. Users can reach the Wiki, Triggers, Events, and Agents pages via the sidebar.

There is also a profile button which provides the entry point for the Admin pages, personal settings, and for signing out.

The chat widget also provides the user with the ability to create new messages or see past messages via the "clock" icon.

To find relevant pages, users can use the search bar which is also available in the left hand sidebar. It provides a search over all of the wiki documents as well as the folders of the wiki.

## Admins

The **Admin** area (visible only to admins) holds general system configuration information. It is useful for getting the system set up for all of the users.

There is no special access priviledge to the admin role as far as being able to audit user interactions, view extra pages of the wiki, see other users' triggers, or anything which may leak sensitive information.

The first account created on a fresh install is auto-promoted to admin. Admins can promote other users to admins.

## Scopes

Wiki pages can be shared with users or groups and can also be made public. The scopes of documents can be managed by users with write access.

Triggers are only visible to the users who created them.

Events are only visible to the users who own the associated triggers.
