on open location theURL
	try
		set bundlePath to POSIX path of (path to me)
		set launcherPath to bundlePath & "Contents/Resources/agentwiki-launcher"
		do shell script "mkdir -p $HOME/.agentwiki"
		do shell script (quoted form of launcherPath) & " dispatch " & (quoted form of theURL) & " >> $HOME/.agentwiki/stub.log 2>&1"
	on error errMsg
		try
			do shell script "mkdir -p $HOME/.agentwiki && echo $(date) error: " & (quoted form of errMsg) & " >> $HOME/.agentwiki/stub.log"
		end try
	end try
end open location

on run
	display dialog "AgentWikiLauncher is installed.

Click Run Agent in your wiki to spawn an agent here." buttons {"OK"} default button "OK" with icon note
end run
