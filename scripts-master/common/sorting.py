# Library for sorting.

class SortKey:
    def __init__(self):
        self.vals = []
        self.reverse = []

    def add_key(self, val, reverse=True):
        self.vals.append(val)
        self.reverse.append(reverse)

    def _cmp(self, other):
        result = 0
        for i in range(len(self.vals)):
            if i >= len(other.vals):
                result = 1
                break
            # XXX reverse on one value and not another acts like negation
            if self.reverse[i] and not other.reverse[i]:
                result = -1
            elif not self.reverse[i] and other.reverse[i]:
                result = 1
            elif self.vals[i] < other.vals[i]:
                result = -1
            elif self.vals[i] > other.vals[i]:
                result = 1
            if self.reverse[i] and other.reverse[i]:
                result = -result
            if result != 0:
                break
        if result == 0 and len(self.vals) < len(other.vals):
            result = -1
        return result

    def __lt__(self, other):
        return self._cmp(other) < 0

    def __gt__(self, other):
        return self._cmp(other) > 0

    def __eq__(self, other):
        return self._cmp(other) == 0

    def __le__(self, other):
        return self._cmp(other) <= 0

    def __ge__(self, other):
        return self._cmp(other) >= 0

    def __ne__(self, other):
        return self._cmp(other) != 0

    def __hash__(self):
        raise TypeError('hash not implemented')

def chronological_order(b, upstream_repo=None, reverse=False):
    '''Generates a chronological key function for testruns.'''
    def key_function(testrun):
        k = SortKey()
        # TODO: Use test_completed_date in newer repo format.
        # XXX: In older format, we lack a good 'date of test' field.
        # Combine a number of factors to approximate chronological order.
        k.add_key(testrun.year_month, reverse=reverse)
        commit_date = None
        not_found = False
        commit_key = None
        if upstream_repo is not None:
            try:
                commit_key = testrun.source_commit
                upstream_commit = upstream_repo.commit(testrun.source_commit)
                commit_date = upstream_commit.committed_date
            except (KeyError, ValueError): # ValueError for upstream_repo lookup
                commit_key = testrun.bunsen_commit_id
                bunsen_commit = b.git_repo.commit(testrun.bunsen_commit_id)
                commit_date = bunsen_commit.committed_date
        else:
            pass # remove commit_date from ALL sort_keys
        k.add_key(commit_key)
        return k
    return key_function

