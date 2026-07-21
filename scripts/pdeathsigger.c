#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <signal.h>
#include <grp.h>
#include <errno.h>

/*
  preexec_fn is not safe to use in the presence of threads,
  child process could deadlock before exec is called.
  this little shim is a workaround to avoid using preexec_fn and
  still get the desired behavior (PR_SET_PDEATHSIG and privilege dropping).

  Usage:
    pdeathsigger /path/to/binary [args...]
    pdeathsigger -u <uid> -g <gid> /path/to/binary [args...]

  When -u and -g are provided, drops privileges to the specified UID/GID
  before executing the command. This prevents privilege escalation if the
  target binary is attacker-controlled.
*/
int main(int argc, char *argv[]) {
  int arg_offset = 1;
  long uid = -1;
  long gid = -1;
  char *endptr;

  /* Parse optional -u and -g flags */
  while (arg_offset < argc) {
    if (strcmp(argv[arg_offset], "-u") == 0 && arg_offset + 1 < argc) {
      errno = 0;
      uid = strtol(argv[arg_offset + 1], &endptr, 10);
      if (errno != 0 || *endptr != '\0' || uid < 0) {
        fprintf(stderr, "Invalid UID: %s\n", argv[arg_offset + 1]);
        return 1;
      }
      arg_offset += 2;
    } else if (strcmp(argv[arg_offset], "-g") == 0 && arg_offset + 1 < argc) {
      errno = 0;
      gid = strtol(argv[arg_offset + 1], &endptr, 10);
      if (errno != 0 || *endptr != '\0' || gid < 0) {
        fprintf(stderr, "Invalid GID: %s\n", argv[arg_offset + 1]);
        return 1;
      }
      arg_offset += 2;
    } else {
      break;
    }
  }

  if (arg_offset >= argc) {
    fprintf(stderr, "Usage: %s [-u <uid> -g <gid>] /path/to/binary [args...]\n", argv[0]);
    return 1;
  }

  /* Set PR_SET_PDEATHSIG */
  if (prctl(PR_SET_PDEATHSIG, SIGKILL) == -1) {
    perror("prctl");
    return 1;
  }

  /* Drop privileges if uid/gid were specified and we're root */
  if (uid >= 0 && gid >= 0 && geteuid() == 0 && uid != 0) {
    /* Drop supplementary groups */
    if (setgroups(0, NULL) == -1) {
      perror("setgroups");
      return 1;
    }

    /* Set GID before UID (can't change GID after dropping root) */
    if (setgid((gid_t)gid) == -1) {
      perror("setgid");
      return 1;
    }

    if (setuid((uid_t)uid) == -1) {
      perror("setuid");
      return 1;
    }
  }

  execvp(argv[arg_offset], &argv[arg_offset]);

  perror("execvp");
  return 1;
}
