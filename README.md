# qbatch

A nicer way to submit lists of commands to SGE/PBS

Some examples: 
```sh 
# submit an array job from a list of commands (one per line)
$ qbatch commands.txt

# set the walltime 
$ qbatch commands.txt -- '#PBS -l walltime=3:00:00'

# run 24 commands per array element
$ qbatch -c24 commands.txt

# run 24 commands per array element, with 12 in parallel 
$ qbatch -c25 -j12 commands.txt
```
