Invariants: individual code section (everyone touches their own code folder, PM should copy the work they need to look further into inside of their own folder, bucketed by research topics); Prod code section (this is for .py files and things like paper trading and testing)

Onboarding instructions...
1) Download VSCode and the git extension
2) Open a terminal and cd into a folder for this project (on your machine) (use the cd command in the terminal to move around and ls to see what you can cd into)
3) Once you're in your folder for the project, input this command to clone the repository on your local machine... git clone https://github.com/rafael-leifert-yale/PGI-Derivatives
4) cd into the repository folder
5) cd into the folder with your name
6) create files to work on

When you want to stop working, but don't want to commit and push to the repo, you can just save locally to your local machine.

When you want to commit and push new code to your folder so everyone can see it, follow this procedure...
First check in github if you were the last commit.
Make sure you are in your folder before doing this...

Case 1: No new updates on git (i.e. you were the last update)
a) git stage .
b) git commit -m "[commit message goes here]"
c) git push origin

Case 2: Some analyst updated already on git (i.e. you were NOT the last update)
a) git stage .
b) git stash

^ the above two steps just "save things that are ready to be pushed on your local"

c) git pull origin (here, if nobody messed with the invariant, nothing bad should happen, i.e. no merge conflicts)
d) git stash pop (pops your local changes back into your staging area)|
e) do case 1