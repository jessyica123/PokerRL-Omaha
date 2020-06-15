# Copyright (c) 2019 Eric Steinberger 2020 Vsevolod Kompantsev


import numpy as np
from scipy.special import comb
import itertools
import time

from PokerRL.game.Poker import Poker
from PokerRL.game.PokerRange import PokerRange
from PokerRL.game._.cpp_wrappers.CppLUT import CppLibHoldemLuts


class _LutGetterBase:

    def __init__(self, rules):
        self.rules = rules

    def get_1d_card_2_2d_card_LUT(self):
        raise NotImplementedError

    def get_2d_card_2_1d_card_LUT(self):
        raise NotImplementedError

    def get_idx_2_hole_card_LUT(self):
        raise NotImplementedError

    def get_hole_card_2_idx_LUT(self):
        raise NotImplementedError

    def get_card_in_what_range_idxs_LUT(self):
        raise NotImplementedError

    def get_range_idx_to_private_obs_LUT(self, preflop_suit_bucketing=False):
        range_idx_to_hc_lut = self.get_idx_2_hole_card_LUT()
        hc_1d_to_2d_lut = self.get_1d_card_2_2d_card_LUT()

        D = self.rules.N_SUITS + self.rules.N_RANKS

        lut = np.zeros(shape=(self.rules.RANGE_SIZE, D * self.rules.N_HOLE_CARDS), dtype=np.float32)

        for range_idx in range(self.rules.RANGE_SIZE):
            priv_o = np.zeros(shape=self.rules.N_HOLE_CARDS * D, dtype=np.float32)

            for c_id in range(self.rules.N_HOLE_CARDS):
                card = hc_1d_to_2d_lut[range_idx_to_hc_lut[range_idx, c_id]]
                priv_o[D * c_id + card[0]] = 1

                # If the suit doesn't matter, it is not included with the observation.
                if self.rules.SUITS_MATTER:
                    if not preflop_suit_bucketing:
                        priv_o[D * c_id + self.rules.N_RANKS + card[1]] = 1
                    else:
                        # here we bucket hands by suits but not setting suit bit at all - do nothing
                        pass

            lut[range_idx] = priv_o

        return lut

    def get_n_boards_LUT(self):
        _c = self.get_n_cards_dealt_in_transition_to_LUT()
        return {
            r: comb(N=self.rules.N_RANKS * self.rules.N_SUITS, k=_c[r], exact=True, repetition=False)
            for r in self.rules.ALL_ROUNDS_LIST
        }

    def get_n_cards_out_at_LUT(self):
        return {
            Poker.PREFLOP: 0,
            Poker.FLOP: self.rules.N_FLOP_CARDS,
            Poker.TURN: self.rules.N_FLOP_CARDS + self.rules.N_TURN_CARDS,
            Poker.RIVER: self.rules.N_FLOP_CARDS + self.rules.N_TURN_CARDS + self.rules.N_RIVER_CARDS,
        }

    def get_n_cards_dealt_in_transition_to_LUT(self):
        return {
            Poker.PREFLOP: 0,
            Poker.FLOP: self.rules.N_FLOP_CARDS,
            Poker.TURN: self.rules.N_TURN_CARDS,
            Poker.RIVER: self.rules.N_RIVER_CARDS,
        }

    def get_n_board_branches_LUT(self):
        _N_CARDS_DEALT_IN_TRANSITION_TO_LUT = self.get_n_cards_dealt_in_transition_to_LUT()
        _N_CARDS_OUT_AT = self.get_n_cards_out_at_LUT()
        lut = {
            Poker.PREFLOP: 0
        }
        for r in [_r for _r in self.rules.ALL_ROUNDS_LIST if _r != Poker.PREFLOP]:
            nc = self.rules.N_CARDS_IN_DECK \
                 - _N_CARDS_OUT_AT[self.rules.ROUND_BEFORE[r]] \
                 - self.rules.N_HOLE_CARDS

            # get_range_size is actually a general combinatorial function that we can also use here
            lut[r] = PokerRange.get_range_size(n_hole_cards=_N_CARDS_DEALT_IN_TRANSITION_TO_LUT[r],
                                               n_cards_in_deck=nc)
        return lut


class _LutGetterHoldem(_LutGetterBase):

    def __init__(self, env_cls):
        super().__init__(rules=env_cls.RULES)
        self.cpp_backend = CppLibHoldemLuts(n_boards_lut=self.get_n_boards_LUT(),
                                            n_cards_out_lut=self.get_n_cards_out_at_LUT())

    def get_1d_card_2_2d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_CARDS_IN_DECK, 2), fill_value=-2, dtype=np.int8)
        for c in range(self.rules.N_CARDS_IN_DECK):
            lut[c] = self.cpp_backend.get_2d_card(c)
        return lut

    def get_2d_card_2_1d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_RANKS, self.rules.N_SUITS), fill_value=-2, dtype=np.int8)
        for r in range(self.rules.N_RANKS):
            for s in range(self.rules.N_SUITS):
                lut[r, s] = self.cpp_backend.get_1d_card(card_2d=np.array([r, s], dtype=np.int8))
        return lut

    def get_idx_2_hole_card_LUT(self):
        return self.cpp_backend.get_idx_2_hole_card_lut()

    def get_hole_card_2_idx_LUT(self):
        return self.cpp_backend.get_hole_card_2_idx_lut()

    def get_card_in_what_range_idxs_LUT(self):
        lut = np.full(shape=(self.rules.N_CARDS_IN_DECK, self.rules.N_CARDS_IN_DECK - 1), fill_value=-2,
                      dtype=np.int32)

        _idx2hc_lut = self.get_idx_2_hole_card_LUT()
        for c in range(self.rules.N_CARDS_IN_DECK):
            n = 0
            for range_idx in range(self.rules.RANGE_SIZE):
                if c in _idx2hc_lut[range_idx]:
                    lut[c, n] = range_idx
                    n += 1

        assert not np.any(lut == -2)
        return lut


class _LutGetterLeduc(_LutGetterBase):

    def __init__(self, env_cls):
        super().__init__(rules=env_cls.RULES)

    def get_1d_card_2_2d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_CARDS_IN_DECK, 2), fill_value=-2, dtype=np.int8)
        for c in range(self.rules.N_CARDS_IN_DECK):
            lut[c] = self._get_2d_card(c)
        return lut

    def get_2d_card_2_1d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_RANKS, self.rules.N_SUITS),
                      fill_value=-2, dtype=np.int8)
        for r in range(self.rules.N_RANKS):
            for s in range(self.rules.N_SUITS):
                lut[r, s] = self._get_1d_card(card_2d=np.array([r, s], dtype=np.int8))
        return lut

    def get_idx_2_hole_card_LUT(self):
        # int between 0 and n_cards * (n_cards-1) inclusive --> [c1]
        return np.expand_dims(np.arange(self.rules.N_CARDS_IN_DECK), axis=1)

    def get_hole_card_2_idx_LUT(self):
        # [c1] --> int between 0 and n_cards * (n_cards-1) inclusive
        return np.expand_dims(np.arange(self.rules.N_CARDS_IN_DECK), axis=1)

    def get_card_in_what_range_idxs_LUT(self):
        return np.arange(self.rules.RANGE_SIZE).reshape(-1, 1)  # 1-card games are easy

    def _get_1d_card(self, card_2d):
        """
        Args:
            card_2d (np.ndarray):    array of 2 int8s. [rank, suit]

        Returns:
            int8: 1d representation of card_2d
        """
        return card_2d[0] * self.rules.N_SUITS + card_2d[1]

    def _get_2d_card(self, card_1d):
        """
        Args:
            card_1d (int):

        Returns:
            np.ndarray(shape=2, dtype=np.int8): 2d representation of card_1d
        """
        card_2d = np.empty(shape=2, dtype=np.int8)
        card_2d[0] = card_1d // self.rules.N_SUITS
        card_2d[1] = card_1d % self.rules.N_SUITS
        return card_2d


class _LutGetterPLO(_LutGetterBase):

    def __init__(self, env_cls):
        super().__init__(rules=env_cls.RULES)
        self.cpp_backend = CppLibHoldemLuts(n_boards_lut=self.get_n_boards_LUT(),
                                            n_cards_out_lut=self.get_n_cards_out_at_LUT())

    def get_range_idx_to_private_obs_LUT(self, preflop_suit_bucketing=False):
        """
        slow version, moved 1d to 2d translation out of cycle,
        unrolled 4-cards loop, no check for SUITS_MATTER cuz its PLO, they DO matter.
        changed from 12 to 5.3 sec, still slow, should be modified with fromiter and generator feeding
        or numba JIT transform
        """

        range_idx_to_hc_lut = self.get_idx_2_hole_card_LUT()
        hc_1d_to_2d_lut = self.get_1d_card_2_2d_card_LUT()

        start = time.process_time()
        D = self.rules.N_SUITS + self.rules.N_RANKS

        lut = np.empty(shape=(self.rules.RANGE_SIZE, D * self.rules.N_HOLE_CARDS), dtype=np.int8)

        # convert array of 1d hands to array of 2d hands
        d2_range_lut = hc_1d_to_2d_lut[range_idx_to_hc_lut]

        if not preflop_suit_bucketing:
            for range_idx, element in enumerate(d2_range_lut):
                priv_o = np.empty(shape=self.rules.N_HOLE_CARDS * D, dtype=np.int8)
                priv_o[D * 0 + element[0, 0]] = 1
                priv_o[D * 0 + self.rules.N_RANKS + element[0, 1]] = 1
                priv_o[D * 1 + element[1, 0]] = 1
                priv_o[D * 1 + self.rules.N_RANKS + element[1, 1]] = 1
                priv_o[D * 2 + element[2, 0]] = 1
                priv_o[D * 2 + self.rules.N_RANKS + element[2, 1]] = 1
                priv_o[D * 3 + element[3, 0]] = 1
                priv_o[D * 3 + self.rules.N_RANKS + element[3, 1]] = 1

                lut[range_idx] = priv_o
        else:

            # for a preflop table we bucket hands, not setting any suit at all so no suit difference
            for range_idx, element in enumerate(d2_range_lut[:, ]):
                priv_o = np.empty(shape=self.rules.N_HOLE_CARDS * D, dtype=np.int8)
                priv_o[D * 0 + element[0, 0]] = 1
                # priv_o[D * 0 + self.rules.N_RANKS + element[0,1]] = 1
                priv_o[D * 1 + element[1, 0]] = 1
                # priv_o[D * 1 + self.rules.N_RANKS + element[0,1]] = 1
                priv_o[D * 2 + element[2, 0]] = 1
                # priv_o[D * 2 + self.rules.N_RANKS + element[0,1]] = 1
                priv_o[D * 3 + element[3, 0]] = 1
                # priv_o[D * 3 + self.rules.N_RANKS + element[0,1]] = 1

                lut[range_idx] = priv_o

        print(f"time elapsed for get_range_idx_to_private_obs_LUT PLO {time.process_time() - start}")

        return lut

    def get_1d_card_2_2d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_CARDS_IN_DECK, 2), fill_value=-2, dtype=np.int8)
        for c in range(self.rules.N_CARDS_IN_DECK):
            lut[c] = self.cpp_backend.get_2d_card(c)
        return lut

    def get_2d_card_2_1d_card_LUT(self):
        lut = np.full(shape=(self.rules.N_RANKS, self.rules.N_SUITS), fill_value=-2, dtype=np.int8)
        for r in range(self.rules.N_RANKS):
            for s in range(self.rules.N_SUITS):
                lut[r, s] = self.cpp_backend.get_1d_card(card_2d=np.array([r, s], dtype=np.int8))
        return lut

    def get_idx_2_hole_card_LUT(self):
        # create np array of card indexes
        indexes = np.arange(0, 52)
        # declare an array for all combibations
        dt = np.dtype([('', indexes.dtype)] * 4)
        # fill array right from combinations() func iteratively - its faster!
        b = np.fromiter(itertools.combinations(indexes, 4), dt)
        # finally reshape it and return
        lut = b.view(indexes.dtype).reshape(-1, 4)
        return lut

    def get_hole_card_2_idx_LUT(self):
        # constructs a LUT which is 4-d array of 52,
        # used with plo 4-card hand (sorted card indexes) returns 1-NUMBER idx of hand
        # reversed version of previous LUT
        cmax = self.rules.N_CARDS_IN_DECK
        lut = np.full(shape=(cmax, cmax,
                             cmax, cmax), fill_value=-2,
                      dtype=np.int32)
        n = 0
        for i1 in range(cmax):
            for i2 in range(i1 + 1, cmax):
                for i3 in range(i2 + 1, cmax):
                    for i4 in range(i3 + 1, cmax):
                        lut[i1, i2, i3, i4] = n
                        n += 1
        return lut

    def get_card_in_what_range_idxs_LUT(self):
        # now fast with numpy, still no idea why we need it
        # also 20825 is a magic constant, got it empirically for PLO 4 cards
        lut = np.full(shape=(self.rules.N_CARDS_IN_DECK, 20825), fill_value=-2,
                      dtype=np.int32)
        _idx2hc_lut = self.get_idx_2_hole_card_LUT()

        for c in range(self.rules.N_CARDS_IN_DECK):
            items = np.where(c == _idx2hc_lut)
            lut[c,] = items[0]

        assert not np.any(lut == -2)
        return lut


class _LutHolderBase:
    """ abstract """

    def __init__(self, lut_getter):
        self._lut_getter = lut_getter

        # lut[i, 0] --> rank; lut[i, 1] --> suit
        self.LUT_1DCARD_2_2DCARD = self._lut_getter.get_1d_card_2_2d_card_LUT()
        # lut[rank, suit] --> int
        self.LUT_2DCARD_2_1DCARD = self._lut_getter.get_2d_card_2_1d_card_LUT()
        # lut[range_idx] -> array of size   n_hole_cards * (n_suits + n_ranks)
        self.LUT_RANGE_IDX_TO_PRIVATE_OBS = self._lut_getter.get_range_idx_to_private_obs_LUT()
        self.LUT_RANGE_IDX_TO_PRIVATE_OBS_PF = self._lut_getter. \
            get_range_idx_to_private_obs_LUT(preflop_suit_bucketing=True)

        self.LUT_IDX_2_HOLE_CARDS = self._lut_getter.get_idx_2_hole_card_LUT()
        self.LUT_HOLE_CARDS_2_IDX = self._lut_getter.get_hole_card_2_idx_LUT()

        # [c] --> list of all range idxs that contain this card.
        self.LUT_CARD_IN_WHAT_RANGE_IDXS = self._lut_getter.get_card_in_what_range_idxs_LUT()

        # [round] -> number of possible public boards in that round
        self.DICT_LUT_N_BOARDS = self._lut_getter.get_n_boards_LUT()

        # [round] -> number of cards that have been dealt until (including) the round
        self.DICT_LUT_N_CARDS_OUT = self._lut_getter.get_n_cards_out_at_LUT()

        # [round] -> number of cards that are dealt in the transition to round
        self.DICT_LUT_CARDS_DEALT_IN_TRANSITION_TO = self._lut_getter.get_n_cards_dealt_in_transition_to_LUT()

        # [round] -> number of possible branches when board is dealt GOING INTO round
        self.DICT_LUT_N_BOARD_BRANCHES = self._lut_getter.get_n_board_branches_LUT()

    def get_1d_card(self, card_2d):
        """
        Args:
            card_2d (np.ndarray):    array of 2 int8s. [rank, suit]

        Returns:
            int8: 1d representation of card_2d
        """

        if card_2d[0] == Poker.CARD_NOT_DEALT_TOKEN_1D:
            return Poker.CARD_NOT_DEALT_TOKEN_1D
        return self.LUT_2DCARD_2_1DCARD[card_2d[0], card_2d[1]]

    def get_1d_cards(self, cards_2d):
        """
        Args:
            cards_2d (iterable):   2D array of shape [N, 2]

        Returns:
            1D array of ints, representing the cards in 1D format
        """
        if len(cards_2d.shape) == 0 or cards_2d.shape[0] == 0:
            return np.array([], dtype=np.int8)

        aa = np.copy(cards_2d)
        aa[aa == Poker.CARD_NOT_DEALT_TOKEN_1D] = 0  # for robustness against not-dealt tokens
        return np.where(cards_2d[:, 0] == Poker.CARD_NOT_DEALT_TOKEN_1D,  # not dealt
                        Poker.CARD_NOT_DEALT_TOKEN_1D,
                        self.LUT_2DCARD_2_1DCARD[aa[:, 0], aa[:, 1]])

    def get_2d_cards(self, cards_1d):
        """
        Args:
            cards_1d (iterable):   list or array of ints. NOT A SINGLE INT!

        Returns:
            2D array of ints representing the cards in 2D format
        """

        if len(cards_1d.shape) == 0 or cards_1d.shape[0] == 0:
            return np.array([], dtype=np.int8)

        aa = np.copy(cards_1d)
        aa[aa == Poker.CARD_NOT_DEALT_TOKEN_1D] = 0  # for robustness against not-dealt tokens
        cards_2d = np.copy(self.LUT_1DCARD_2_2DCARD[aa]).reshape(-1, 2)
        cards_2d[np.where(cards_1d == Poker.CARD_NOT_DEALT_TOKEN_1D)] = Poker.CARD_NOT_DEALT_TOKEN_2D.reshape(2)
        return cards_2d

    def get_range_idx_from_hole_cards(self, hole_cards_2d):
        raise NotImplementedError

    def get_2d_hole_cards_from_range_idx(self, range_idx):
        raise NotImplementedError

    def get_1d_hole_cards_from_range_idx(self, range_idx):
        raise NotImplementedError


class LutHolderLeduc(_LutHolderBase):
    """
    Don't use LUTs from outside this class. use the functions instad!
    """

    def __init__(self, env_cls):
        super().__init__(lut_getter=_LutGetterLeduc(env_cls=env_cls))

    def get_range_idx_from_hole_cards(self, hole_cards_2d):
        c1 = self.get_1d_cards(hole_cards_2d)[0]
        return self.LUT_HOLE_CARDS_2_IDX[c1, 0]

    def get_2d_hole_cards_from_range_idx(self, range_idx):
        c1 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 0]
        return np.array([self.LUT_1DCARD_2_2DCARD[c1]], dtype=np.int8)

    def get_1d_hole_cards_from_range_idx(self, range_idx):
        return np.copy(self.LUT_IDX_2_HOLE_CARDS[range_idx])


class LutHolderHoldem(_LutHolderBase):

    def __init__(self, env_cls):
        super().__init__(lut_getter=_LutGetterHoldem(env_cls=env_cls))

    def get_range_idx_from_hole_cards(self, hole_cards_2d):
        _c1 = self.LUT_2DCARD_2_1DCARD[hole_cards_2d[0, 0]][hole_cards_2d[0, 1]]
        _c2 = self.LUT_2DCARD_2_1DCARD[hole_cards_2d[1, 0]][hole_cards_2d[1, 1]]

        # c1 can never equal c2
        c1 = min(_c1, _c2)
        c2 = max(_c1, _c2)

        return self.LUT_HOLE_CARDS_2_IDX[c1, c2]

    def get_2d_hole_cards_from_range_idx(self, range_idx):
        c1 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 0]
        c2 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 1]

        return np.array([self.LUT_1DCARD_2_2DCARD[c1], self.LUT_1DCARD_2_2DCARD[c2]], dtype=np.int8)

    def get_1d_hole_cards_from_range_idx(self, range_idx):
        return np.copy(self.LUT_IDX_2_HOLE_CARDS[range_idx])


class LutHolderPLO(_LutHolderBase):

    def __init__(self, env_cls):
        super().__init__(lut_getter=_LutGetterPLO(env_cls=env_cls))

    def get_range_idx_from_hole_cards(self, hole_cards_2d):
        list = []
        # sort it, cuz our array is sorted too
        for c in hole_cards_2d:
            list.append(self.LUT_2DCARD_2_1DCARD[c[0]][c[1]])
        list.sort()
        hc = self.LUT_HOLE_CARDS_2_IDX[list[0], list[1], list[2], list[3]]

        return hc

    def get_2d_hole_cards_from_range_idx(self, range_idx):
        c1 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 0]
        c2 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 1]
        c3 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 2]
        c4 = self.LUT_IDX_2_HOLE_CARDS[range_idx, 3]
        hc_2d = np.array([self.LUT_1DCARD_2_2DCARD[c1], self.LUT_1DCARD_2_2DCARD[c2],
                          self.LUT_1DCARD_2_2DCARD[c3], self.LUT_1DCARD_2_2DCARD[c4]], dtype=np.int8)
        return hc_2d

    def get_1d_hole_cards_from_range_idx(self, range_idx):
        return np.copy(self.LUT_IDX_2_HOLE_CARDS[range_idx])
