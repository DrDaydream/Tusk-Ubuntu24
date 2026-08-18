use crypto::PublicKey;
use ed25519_dalek::{Digest as _, Sha512};
use std::convert::TryInto as _;

fn score(authority: &PublicKey, round: u64, seed: u64, domain: u8) -> [u8; 32] {
    let mut hasher = Sha512::new();
    hasher.update(b"narwhal-dynamic-adversary-v1");
    hasher.update(seed.to_le_bytes());
    hasher.update(round.to_le_bytes());
    hasher.update([domain]);
    hasher.update(authority.as_ref());
    let output = hasher.finalize();
    output[..32].try_into().unwrap()
}

pub fn selected(
    authority: &PublicKey,
    authorities: &[PublicKey],
    round: u64,
    faults: usize,
    seed: u64,
) -> bool {
    let faults = faults.min(authorities.len());
    if faults == 0 {
        return false;
    }

    if round % 2 == 0 {
        let window = 3 * faults + 1;
        let ordinal = round / 2;
        let window_start = ((ordinal.saturating_sub(1) as usize) / window) * window + 1;
        let mut leader_slots = (0..window)
            .map(|offset| {
                let candidate_round = 2 * (window_start + offset) as u64;
                let leader = authorities[candidate_round as usize % authorities.len()];
                (score(&leader, candidate_round, seed, 2), candidate_round)
            })
            .collect::<Vec<_>>();
        leader_slots.sort_unstable();
        let leader_is_silent = leader_slots
            .into_iter()
            .take(faults)
            .any(|(_, candidate_round)| candidate_round == round);
        let leader = authorities[round as usize % authorities.len()];
        if *authority == leader {
            return leader_is_silent;
        }
        let remaining = faults - usize::from(leader_is_silent);
        let own_key = (score(authority, round, seed, 0), *authority);
        let rank = authorities
            .iter()
            .filter(|candidate| **candidate != leader)
            .filter(|candidate| (score(candidate, round, seed, 0), **candidate) < own_key)
            .count();
        return rank < remaining;
    }

    let own_key = (score(authority, round, seed, 0), *authority);
    let rank = authorities
        .iter()
        .filter(|candidate| (score(candidate, round, seed, 0), **candidate) < own_key)
        .count();
    rank < faults
}

/// Select direct-commit slots in deterministic windows of 3f+1 leader rounds.
/// Silent leaders are excluded before the f+2 lowest-scored available slots
/// are selected, so a full window reaches the requested (f+2)/(3f+1) ratio.
pub fn direct_commit_selected(
    authorities: &[PublicKey],
    leader_round: u64,
    faults: usize,
    seed: u64,
) -> bool {
    if faults == 0 || authorities.is_empty() {
        return true;
    }

    let window = 3 * faults + 1;
    let target = (faults + 2).min(window);
    let ordinal = leader_round / 2;
    let window_start = ((ordinal.saturating_sub(1) as usize) / window) * window + 1;
    let mut available = Vec::with_capacity(window);

    for offset in 0..window {
        let candidate_round = 2 * (window_start + offset) as u64;
        let leader = authorities[candidate_round as usize % authorities.len()];
        if !selected(&leader, authorities, candidate_round, faults, seed) {
            available.push((score(&leader, candidate_round, seed, 1), candidate_round));
        }
    }
    available.sort_unstable();
    available
        .into_iter()
        .take(target)
        .any(|(_, round)| round == leader_round)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn authorities(count: u8) -> Vec<PublicKey> {
        (0..count).map(|value| PublicKey([value; 32])).collect()
    }

    #[test]
    fn selects_exactly_f_adversaries_per_round() {
        let authorities = authorities(10);
        for round in 1..50 {
            let selected = authorities
                .iter()
                .filter(|authority| selected(authority, &authorities, round, 3, 7))
                .count();
            assert_eq!(selected, 3);
        }
    }

    #[test]
    fn schedule_is_reproducible_and_seeded() {
        let authorities = authorities(10);
        let schedule = |seed| {
            (1..30)
                .map(|round| {
                    authorities
                        .iter()
                        .filter(|authority| selected(authority, &authorities, round, 3, seed))
                        .copied()
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>()
        };
        assert_eq!(schedule(11), schedule(11));
        assert_ne!(schedule(11), schedule(12));
    }

    #[test]
    fn direct_slots_match_requested_ratio_when_enough_leaders_are_available() {
        let authorities = authorities(10);
        let faults = 3;
        let window = 3 * faults + 1;
        for block in 0..20 {
            let selected = (0..window)
                .map(|offset| 2 * (block * window + offset + 1) as u64)
                .filter(|round| direct_commit_selected(&authorities, *round, faults, 19))
                .count();
            assert_eq!(selected, faults + 2);
        }
    }
}
