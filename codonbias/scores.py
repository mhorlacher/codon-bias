from collections import Counter
from itertools import product
import os

import numpy as np
import pandas as pd
from scipy import stats

from .stats import CodonCounter, NucleotideCounter
from .utils import fetch_GCN_from_GtRNAdb, geomean, mean, reverse_complement


class ScalarScore(object):
    """
    Abstract class for models that output a scalar per sequence.
    Inheriting classes may implement the computation of the score for
    a single sequence in the method `_calc_score(seq)`. Parameters
    of the model may be initialized with the instance of the class.
    """
    def __init__(self):
        pass

    def get_score(self, seq, slice=None, **kwargs):
        """
        Compute the score for a single, or multiple sequences. When
        `slice` is provided, all sequences will be sliced before
        computing the score.

        Parameters
        ----------
        seq : str or an iterable of str
            DNA sequence, or an iterable of ones.
        slice : slice, optional
            Python slice object, by default None

        Returns
        -------
        float or numpy.array
            _description_

        Examples
        --------
        >>> EffectiveNumberOfCodons().get_score('ACGACGGAGGAG')
        35.0

        >>> EffectiveNumberOfCodons().get_score('ACGACGGAGGAG', slice=slice(6))
        44.33333333333333
        """
        if not isinstance(seq, str):
            return np.array([self.get_score(s, slice=slice, **kwargs) for s in seq])

        if slice is not None:
            return self._calc_score(seq[slice], **kwargs)
        else:
            return self._calc_score(seq, **kwargs)

    def _calc_score(self, seq):
        raise Exception('not implemented')


class VectorScore(object):
    """
    Abstract class for models that output a vector per sequence. For
    example, the output can be a score per position in the sequence.
    Inheriting classes may implement the computation of the score for
    a single sequence in the method `_calc_vector(seq)`. Parameters
    of the model may be initialized with the instance of the class.
    """
    def __init__(self):
        pass

    def get_vector(self, seq, slice=None, **kwargs):
        """
        Compute the score vector for a single, or multiple sequences.
        When `slice` is provided, all sequences will be sliced before
        computing the score.

        Parameters
        ----------
        seq : str or an iterable of str
            DNA sequence, or an iterable of ones.
        slice : slice, optional
            Python slice object, by default None

        Returns
        -------
        numpy.array, or numpy.array of numpy.array
            1D array for a single sequence, 1D array of 1D arrays for
            arbitrary sequences, or a matrix NxM for N sequences of length
            M.
        """
        if not isinstance(seq, str):
            return np.array([self.get_vector(s, slice=slice, **kwargs) for s in seq])

        if slice is not None:
            return self._calc_vector(seq[slice], **kwargs)
        else:
            return self._calc_vector(seq, **kwargs)

    def _calc_vector(self, seq):
        raise Exception('not implemented')

    def _get_codon_vector(self, seq, k_mer=1):
        return [seq[i:i+3*k_mer] for i in range(0, len(seq), 3)]


class FrequencyOfOptimalCodons(ScalarScore, VectorScore):
    """
    Frequency of Optimal Codons (FOP, Ikemura, J Mol Biol, 1981).

    This model determines the optimal codons for each amino acid based
    on their frequency in the given set of reference sequences
    `ref_seq`. Multiple codons may be selected as optimal based on
    `thresh`. The score for a sequence is the fraction of codons in
    the sequence deemed optimal. The returned vector for a sequence is
    a binary array where optimal positions contain 1 and non-optimal
    ones contain 0.

    Parameters
    ----------
    ref_seq : iterable of str
        A set of reference DNA sequences for codon usage statistics.
    thresh : float, optional
        Minimal ratio between the frequency of a codon and the most
        frequent one in order to be set as optimal, by default 0.95
    genetic_code : int, optional
        NCBI genetic code ID, by default 1
    ignore_stop : bool, optional
        Whether STOP codons will be discarded from the analysis, by
        default True
    pseudocount : int, optional
        Pseudocount correction for normalized codon frequencies. this is
        effective when `ref_seq` contains few short sequences. by default 1
    """
    def __init__(self, ref_seq, thresh=0.95, genetic_code=1,
                 ignore_stop=True, pseudocount=1):
        self.thresh = thresh
        self.counter = CodonCounter(genetic_code=genetic_code,
                                    ignore_stop=ignore_stop)
        self.pseudocount = pseudocount

        self.weights = self.counter.count(ref_seq)\
            .get_aa_table(normed=True, pseudocount=pseudocount)\
            .groupby('aa').apply(lambda x: x / x.max())
        self.weights[self.weights >= self.thresh] = 1  # optimal
        self.weights[self.weights < self.thresh] = 0  # non-optimal
        self.weights = self.weights.droplevel('aa')

    def _calc_score(self, seq):
        counts = self.counter.count(seq).counts

        return mean(self.weights, counts)

    def _calc_vector(self, seq):
        return self.weights.reindex(self._get_codon_vector(seq)).values


class RelativeSynonymousCodonUsage(ScalarScore, VectorScore):
    """
    Relative Synonymous Codon Usage (RSCU, Sharp & Li, NAR, 1986).

    This model measures the deviation of synonymous codon usage from
    uniformity and returns for each codon the ratio between its
    observed frequency and its expected frequency if synonymous codons
    were chosen randomly (uniformly). Overepresented codons will have
    a score > 1, while underrepresented codons will have a score < 1.
    `get_weights()` returns a vector of 61 RSCU ratios for each sequence.
    While not defined as part of the original Sharp & Li model, the
    `get_vector()` method returns an array with the ratio of the
    corresponding codon in each position in the sequence, and the
    `get_score()` method returns the geometric mean of the ratios for a
    sequence (minus 1), in a similar way to the Relative Codon Bias Score
    (RCBS). The `directional` parameter modifies RSCU similarly to the way
    the Directional Codon Bias Score (DCBS) modifies RCBS, by giving
    higher weights to both overrepresented and underrepresented codons.

    Parameters
    ----------
    ref_seq : iterable of str, optional
        When given, codon frequencies in the reference set
        will be used instead of the uniform codon distribution,
        by default None
    directional : bool, optional
        When True will compute the modified version by Sabi & Tuller, by
        default False
    mean : {'geometric', 'arithmetic'}, optional
        How to compute the score, by default 'geometric'
    genetic_code : int, optional
        NCBI genetic code ID, by default 1
    ignore_stop : bool, optional
        Whether STOP codons will be discarded from the analysis, by
        default True
    pseudocount : int, optional
        Pseudocount correction for normalized codon frequencies, by
        default 1

    See Also
    --------
    scores.EffectiveNumberOfCodons
    scores.RelativeCodonBiasScore
    """
    def __init__(self, ref_seq=None, directional=False, mean='geometric',
                 genetic_code=1, ignore_stop=True, pseudocount=1):
        self.directional = directional
        self.mean = mean
        self.counter = CodonCounter(sum_seqs=False, genetic_code=genetic_code,
                                    ignore_stop=ignore_stop)
        self.pseudocount = pseudocount

        if ref_seq is None:
            self.reference = CodonCounter('', genetic_code=genetic_code,
                                          ignore_stop=ignore_stop)
        else:
            self.reference = CodonCounter(ref_seq, genetic_code=genetic_code,
                                          ignore_stop=ignore_stop)
        self.reference = self.reference.get_aa_table(
            normed=True, pseudocount=pseudocount)

    def _calc_score(self, seq):
        D = self._calc_weights(seq).droplevel('aa')
        counts = self.counter.counts  # counts have already been prepared in _calc_weights

        if self.mean == 'geometric':
            return geomean(np.log(D), counts) - 1
        elif self.mean == 'arithmetic':
            return mean(D, counts)
        else:
            raise Exception(f'unknown mean: {self.mean}')

    def _calc_vector(self, seq):
        weights = self._calc_weights(seq).droplevel('aa')
        return weights.reindex(self._get_codon_vector(seq)).values

    def get_weights(self, seq):
        """
        Compute a vector of 61 RSCU codon weights (ratios) for each
        sequence in `seq`.

        Parameters
        ----------
        seq : str, or iterable of str
            DNA sequence, or an iterable of ones.

        Returns
        -------
        pandas.Series or pandas.DataFrame
            RSCU weights for each codon, for each sequence.
        """
        return self._calc_weights(seq)

    def _calc_weights(self, seq):
        P = self.counter.count(seq)\
            .get_aa_table(normed=True, pseudocount=self.pseudocount)
        # codon weights
        if self.directional:
            D = np.maximum(
                P.divide(self.reference, axis=0),
                self.reference.divide(P, axis=0))
        else:
            D = P.divide(self.reference, axis=0)

        return D


class CodonAdaptationIndex(ScalarScore, VectorScore):
    """
    Codon Adaptation Index (CAI, Sharp & Li, NAR, 1987).

    This model determines the level of optimality of codons based on
    their frequency in the given set of reference sequences `ref_seq`.
    For each amino acid, the most frequent synonymous codon receives
    a weight of 1, while other codons are weighted based on their
    relative frequency with respect to the most frequent synonymous
    codon. The returned vector for a sequence is an array with the
    weight of the corresponding codon in each position in the
    sequence. The score for a sequence is the geometric mean of these
    weights, and ranges from 0 (strong rare codon bias) to 1 (strong
    frequent codon bias).

    This implementation extends the model to arbitrary codon k-mers
    using the `k_mer` parameter.

    Parameters
    ----------
    ref_seq : iterable of str
        Reference sequences for learning the codon frequencies.
    k_mer : int, optional
        Determines the length of the k-mer to base statistics on, by
        default 1
    genetic_code : int, optional
        NCBI genetic code ID, by default 1
    ignore_stop : bool, optional
        Whether STOP codons will be discarded from the analysis, by
        default True
    pseudocount : int, optional
        Pseudocount correction for normalized codon frequencies. this is
        effective when `ref_seq` contains few short sequences. by default 1
    """
    def __init__(self, ref_seq, k_mer=1, genetic_code=1,
                 ignore_stop=True, pseudocount=1):
        self.counter = CodonCounter(k_mer=k_mer,
                                    genetic_code=genetic_code,
                                    ignore_stop=ignore_stop)
        self.k_mer = k_mer
        self.pseudocount = pseudocount

        self._calc_weights(ref_seq)

    def _calc_score(self, seq):
        counts = self.counter.count(seq).counts

        return geomean(self.log_weights, counts)

    def _calc_vector(self, seq):
        return self.sticky_weights.reindex(
            self._get_codon_vector(seq, k_mer=self.k_mer)).values

    def _calc_weights(self, seqs):
        self.weights = self.counter.count(seqs)\
            .get_aa_table(normed=True, pseudocount=self.pseudocount)

        aa_levels = [n for n in self.weights.index.names if 'aa' in n]
        self.weights = self.weights.groupby(aa_levels)\
            .apply(lambda x: x / x.max()).droplevel(aa_levels)

        self.log_weights = np.log(self.weights)
        self.sticky_weights = self.weights.copy()
        self.sticky_weights.index = self.weights.index.to_series()\
            .str.join('')


class EffectiveNumberOfCodons(ScalarScore):
    """
    Effective Number of Codons (ENC, Wright, Gene, 1990).

    This model measures the deviation of synonymous codon usage from
    uniformity based on a statistical model analogous to the effective
    number of alleles in genetics. The score for a sequence is the
    effective number of codons in use, and ranges from 20 (very strong
    bias: a single codon per amino acid) to 61 (uniform use of all
    codons). Thus, this score is expected to be negatively correlated
    with most other codon bias measures.

    When `bg_correction` is True, a background correction procedure is
    performed as proposed by Novembre (MBE, 2002). This procedure
    estimates the background codon composition of each sequence using
    the independent probabilities of observing each of the 4 bases in
    the 3 codon positions. This implementation learns the nucleotide
    probabilities from the provided coding sequence. However, if the
    parameter `background` is given to get_score(), this background
    sequence will be used instead.

    Parameters
    ----------
    bg_correction : bool, optional
        Background correction based on Novembre (MBE, 2002), by default
        False
    genetic_code : int, optional
        NCBI genetic code ID, by default 1

    See Also
    --------
    scores.RelativeSynonymousCodonUsage
    scores.RelativeCodonBiasScore
    """
    def __init__(self, bg_correction=False, genetic_code=1):
        self.bg_correction = bg_correction
        self.counter = CodonCounter(genetic_code=genetic_code,
                                    ignore_stop=True)  # score is not defined for STOP codons

        self.template = self.counter.count('').get_aa_table().to_frame()
        self.aa_deg = self.template.groupby('aa').size()

        self.BCC_unif = self._calc_BCC(self._calc_BNC(''))

    def _calc_score(self, seq, background=None):
        if background is None:
            background = seq
        counts = self.counter.count(seq).get_aa_table()

        N = counts.groupby('aa').sum()
        P = counts / N

        if self.bg_correction:
            BCC = self._calc_BCC(self._calc_BNC(background))
        else:
            BCC = self.BCC_unif
        chi2 = N * ((P - BCC)**2 / BCC).groupby('aa').sum()  # Novembre 2002
        F = ((chi2 + N - self.aa_deg) / (N - 1) / self.aa_deg).to_frame('F')

        F['deg'] = self.aa_deg
        deg_count = F.groupby('deg').size().to_frame('deg_count')

        # at least 2 samples from AA to be included
        F = F.loc[(N > 1) & (F['F'] > 1e-6) & np.isfinite(F['F'])]\
            .groupby('deg').mean().join(deg_count, how='right')

        # missing AA cases
        miss_3 = np.isnan(F.loc[3, 'F'])
        F['F'] = F['F'].fillna(1/F.index.to_series())  # use 1/deg
        if miss_3:
            F.loc[3, 'F'] = 0.5*(F.loc[2, 'F'] + F.loc[4, 'F'])

        ENC = (F['deg_count'] / F['F']).sum()
        return min([len(P), ENC])

    def _calc_BNC(self, seq):
        """ Compute the background NUCLEOTIDE composition of the sequence. """
        BNC = NucleotideCounter(seq).get_table(normed=True)

        return BNC

    def _calc_BCC(self, BNC):
        """ Compute the background CODON composition of the sequence. """
        BCC = pd.DataFrame(
            [(c1+c2+c3, BNC[c1] * BNC[c2] * BNC[c3])
             for c1, c2, c3 in product('ACGT', 'ACGT', 'ACGT')],
            columns=['codon', 'bcc'])
        BCC = BCC.set_index('codon')['bcc']
        BCC = self.template.join(BCC)['bcc']
        BCC /= BCC.groupby('aa').sum()

        return BCC 


class TrnaAdaptationIndex(ScalarScore, VectorScore):
    """
    tRNA Adaptation Index (tAI, dos Reis, Savva & Wernisch, NAR, 2004).

    This model measures translational efficiency based on the
    availablity of tRNAs (approximated by the gene copy number of each
    tRNA species), and the efficiency of coupling between tRNAs and
    codons (modeled via the set of `s_values` coefficients). Each codon
    receives a weight in [0, 1] that describes its translational
    efficiency. The returned vector for a sequence is an array with
    the weight of the corresponding codon in each position in the
    sequence. The score for a sequence is the geometric mean of these
    weights, and ranges from 0 (low efficiency) to 1 (high efficiency).

    Gene copy numbers can be provided explicitly, or automatically
    downloaded from GtRNAdb.

    The model was originally trained in S. cerevisiae and E. coli
    in order to maximize the correlation with mRNA levels measured via
    microarrays. The model was later refitted using protein abundance
    levels (Tuller et al., Genome Biology, 2011). The `s_values`
    parameter can be used to switch between these coefficients sets.
    When analyzing an organism that is a prokaryote, the `prokaryote`
    parameter should be set to True.

    Parameters
    ----------
    tGCN : pandas.DataFrame, optional
        tRNA Gene Copy Numbers given as a DataFrame with the columns
        `anti_codon`, `GCN`, by default None
    url : str, optional
        URL of the relevant page on GtRNAdb, by default None
    genome_id : str, optional
        Genome ID of the organism, by default None
    domain : str, optional
        Taxonomic domain of the organism, by default None
    prokaryote : bool, optional
        Whether the organism is a prokaryote, by default False
    s_values : {'dosReis', 'Tuller'}, optional
        Coefficients of the tRNA-codon efficiency of coupling, by default 'dosReis'
    genetic_code : int, optional
        NCBI genetic code ID, by default 1

    Notes
    -----
    For species-specific optimization of the tAI model, see:
    Sabi & Tuller, DNA Research, 2014;
    the stAIcalc online calculator: https://tau-tai.azurewebsites.net/;
    and the gtAI package: https://github.com/AliYoussef96/gtAI.

    """
    def __init__(self, tGCN=None, url=None, genome_id=None, domain=None,
                 prokaryote=False, s_values='dosReis', genetic_code=1):
        self.counter = CodonCounter(genetic_code=genetic_code,
                                    ignore_stop=True)  # score is not defined for STOP codons

        # tRNA gene copy numbers of the organism
        if url is not None or (genome_id is not None and domain is not None):
            tGCN = fetch_GCN_from_GtRNAdb(url=url, domain=domain, genome=genome_id)
        if tGCN is None:
            raise Exception('must provide either: tGCN dataframe, GtRNAdb url, or GtRNAdb genome_id+domain')
        tGCN['anti_codon'] = tGCN['anti_codon'].str.upper().str.replace('U', 'T')
        self.tGCN = tGCN

        # S-values: tRNA-codon efficiency of coupling
        self.s_values = pd.read_csv(
            f'{os.path.dirname(__file__)}/tAI_svalues_{s_values}.csv',
            dtype={'weight': float, 'prokaryote': bool}, comment='#')
        self.s_values['anti'] = self.s_values['anti'].str.upper().str.replace('U', 'T')
        self.s_values['cod'] = self.s_values['cod'].str.upper().str.replace('U', 'T')
        if not prokaryote:
            self.s_values = self.s_values.loc[~self.s_values['prokaryote']]

        self.weights = self._calc_weights()
        self.log_weights = np.log(self.weights)

    def _calc_weights(self):
        # init the dataframe
        weights = self.counter.count('').get_aa_table().to_frame('count')
        weights = weights.join(weights.groupby('aa').size().to_frame('deg'))\
            .reset_index().drop(columns=['aa'])[['codon', 'deg']]
        # columns: codon, deg

        # match all possible tRNAs to codons by the 1st,2nd positions
        weights['cod_12'] = weights['codon'].str[:2]
        self.tGCN['cod_12'] = self.tGCN['anti_codon'].apply(reverse_complement).str[:2]
        weights = weights.merge(self.tGCN, on='cod_12')
        # columns: codon, deg, cod_12, anti_codon, GCN

        # match all possible pairs to S-values by the 3rd position
        weights['anti'] = weights['anti_codon'].str[0]
        weights['cod'] = weights['codon'].str[-1]
        weights = weights.merge(self.s_values, on=['anti', 'cod'])
        weights = weights.loc[weights['deg'] >= weights['min_deg']]
        # columns: codon, deg, cod_12, anti_codon, GCN,
        #          anti, cod, min_deg, weight, prokaryote

        weights['weight'] = (1 - weights['weight']) * weights['GCN']
        weights = weights.groupby('codon')['weight'].sum()

        weights /= weights.max()
        weights[weights == 0] = stats.gmean(
            weights[(weights != 0) & np.isfinite(weights)])

        return weights

    def _calc_score(self, seq):
        counts = self.counter.count(seq).counts

        return geomean(self.log_weights, counts)

    def _calc_vector(self, seq):
        return self.weights.reindex(self._get_codon_vector(seq)).values


class CodonPairBias(ScalarScore, VectorScore):
    """
    Codon Pair Bias (CPB/CPS, Coleman et al., Science, 2008).

    This model is extended here to arbitrary codon k-mers. The model
    calculates the over-/under- represention of codon k-mers compared
    to a background distribution. Each k-mer receives a weight that is the
    log-ratio between its observed and expected probabilities. The
    returned vector for a sequence is an array with the weight of the
    corresponding k-mer in each position in the sequence. The score for a
    sequence is the mean of these weights, and ranges from a negative
    value (mostly under-represented pairs) to a positive value (mostly
    over-represented pairs).

    Parameters
    ----------
    ref_seq : iterable of str
        Reference sequences for learning the codon frequencies.
    k_mer : int, optional
        Determines the length of the k-mer to base statistics on, by
        default 2
    genetic_code : int, optional
        NCBI genetic code ID, by default 1
    ignore_stop : bool, optional
        Whether STOP codons will be discarded from the analysis, by
        default True
    pseudocount : int, optional
        Pseudocount correction for normalized codon frequencies. this is
        effective when `ref_seq` contains few short sequences. by default 1
    """
    def __init__(self, ref_seq, k_mer=2, genetic_code=1,
                 ignore_stop=True, pseudocount=1):
        self.counter = CodonCounter(k_mer=k_mer,
            genetic_code=genetic_code, ignore_stop=ignore_stop)
        self.k_mer = k_mer
        self.pseudocount = pseudocount

        self._calc_weights(ref_seq)

    def _calc_score(self, seq):
        counts = self.counter.count(seq).counts

        return mean(self.weights, counts)

    def _calc_vector(self, seq):
        return self.sticky_weights.reindex(
            self._get_codon_vector(seq, k_mer=self.k_mer)).values

    def _calc_weights(self, seq):
        """
        Calculates the Codon Pair Score (CPS) for each pair (or k-mer).
        That is, the log-ratios of observed over expected frequencies.
        """
        weights = self.counter.count(seq).get_aa_table().to_frame('count')
        aa_levels = [n for n in weights.index.names if 'aa' in n]
        cod_levels = [n for n in weights.index.names if 'codon' in n]

        weights['count'] += self.pseudocount
        weights = self._calc_freq(weights, 'aa')
        weights = self._calc_freq(weights, 'codon')

        weights = self._calc_enrichment(weights)\
            .droplevel(aa_levels).reorder_levels(cod_levels)

        self.weights = weights['log_ratio']
        self.sticky_weights = self.weights.copy()
        self.sticky_weights.index = self.weights.index.to_series()\
            .str.join('')

    def _calc_freq(self, counts, word='aa'):
        levels = [n for n in counts.index.names if word in n]

        # calculate k-mer frequencies
        freq_kmer = counts.groupby(levels)['count'].sum()
        freq_kmer /= freq_kmer.sum()
        counts = counts.join(freq_kmer.to_frame(f'freq_{word}_mer'))

        # calculate global frequencies of each "word"
        glob_count = []
        for l in levels:
            glob_count.append(counts.groupby(l)['count'].sum())
        glob_count = pd.concat(glob_count, axis=1).sum(axis=1)
        glob_count /= glob_count.sum()

        # join with the counts dataframe
        for l in levels:
            counts = counts.join(
                glob_count.rename_axis(index=l).to_frame(l))

        # calculate independent joint probabilities
        counts = counts.join(counts[levels].prod(axis=1)
                             .to_frame(f'freq_{word}_ind'))

        return counts

    def _calc_enrichment(self, freqs):
        """
        Calculates the log-ratio between observed k-mer frequencies and
        expected frequencies under the assumption that k-mers are
        distributed independently.
        """
        freqs['log_ratio'] = \
            np.log(freqs['freq_codon_mer'] / freqs['freq_codon_ind']
                   * freqs['freq_aa_ind'] / freqs['freq_aa_mer'])
        return freqs


class RelativeCodonBiasScore(ScalarScore, VectorScore):
    """
    Relative Codon Bias Score (RCBS, Roymondal, Das & Sahoo, DNA Research, 2009).

    This model measures the deviation of codon usage from a background
    distribution and computes for each codon the observed-to-expected
    ratio. The background distribution is estimated for each sequence
    separately, based on its nucleotide composition. The model's null
    hypothesis is that the 3 codon positions are independently
    distributed according to the same nucleotide distribution. Thus,
    overrepresented codons are given higher weights while
    underrepresented codons are given lower weights. The score for a
    sequence is the geometric mean of codon ratios, minus 1. The
    returned vector for a sequence is an array with the ratio of the
    corresponding codon in each position in the sequence.

    Sabi & Tuller (DNA Research, 2014) proposed a modified score based
    on these principles, termed the Directional Codon Bias Score (DCBS).
    In this model underrepresented codons are given larger weights
    (rather than smaller weights) similarly to overrepresnted codons.
    This model's hypothesis is that biased sequences will typically
    include both highly overrepresnted codons as well as
    underrepresented ones, and therefore both signals should
    contribute towards a higher (i.e., biased) score. This
    modification is activated by setting the `directional` parameter
    to True and the `mean` parameter to 'arithmetic'.

    Parameters
    ----------
    directional : bool, optional
        When True will compute the modified version by Sabi & Tuller, by
        default False
    mean : {'geometric', 'arithmetic'}, optional
        How to compute the score, by default 'geometric'
    genetic_code : int, optional
        NCBI genetic code ID, by default 1
    ignore_stop : bool, optional
        Whether STOP codons will be discarded from the analysis, by
        default True
    pseudocount : int, optional
        Pseudocount correction for normalized codon frequencies, by
        default 1

    See Also
    --------
    scores.RelativeSynonymousCodonUsage
    scores.EffectiveNumberOfCodons
    """
    def __init__(self, directional=False, mean='geometric',
                 genetic_code=1, ignore_stop=True, pseudocount=1):
        self.directional = directional
        self.mean = mean
        self.pseudocount = pseudocount
        self.counter = CodonCounter(genetic_code=genetic_code,
                                    ignore_stop=ignore_stop)

    def _calc_score(self, seq):
        D = self._calc_weights(seq)
        counts = self.counter.counts  # counts have already been prepared in _calc_weights

        if self.mean == 'geometric':
            return geomean(np.log(D), counts) - 1
        elif self.mean == 'arithmetic':
            return mean(D, counts)
        else:
            raise Exception(f'unknown mean: {self.mean}')

    def _calc_vector(self, seq):
        D = self._calc_weights(seq)

        return D.reindex(self._get_codon_vector(seq)).values

    def _calc_weights(self, seq):
        counts = self.counter.count(seq)
        # background probabilities
        BCC = self._calc_BCC(self._calc_BNC(seq))
        # observed probabilities
        P = counts.get_codon_table(normed=True, pseudocount=self.pseudocount)
        # codon weights
        if self.directional:
            D = np.maximum(P / BCC, BCC / P)
        else:
            D = P / BCC

        return D

    def _calc_BNC(self, seq):
        """ Compute the background NUCLEOTIDE composition of the sequence. """
        BNC = NucleotideCounter([seq[i::3] for i in range(3)],
                                sum_seqs=False).get_table()

        return BNC

    def _calc_BCC(self, BNC):
        """ Compute the background CODON composition of the sequence. """
        BCC = pd.DataFrame(
            [(c1+c2+c3, BNC[0][c1] * BNC[1][c2] * BNC[2][c3])
             for c1, c2, c3 in product('ACGT', 'ACGT', 'ACGT')],
            columns=['codon', 'bcc'])
        BCC = BCC.set_index('codon')['bcc']
        BCC /= BCC.sum()

        return BCC 
