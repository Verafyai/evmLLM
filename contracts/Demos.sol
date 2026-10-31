// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

// Ten demo contracts for EVMLLM. No mappings: storage is fixed slots and fixed-size arrays,
// because the in-weights EVM build has no KECCAK256 yet.

/// 1. Spend cap: amounts are USD with 6 decimals (like USDC). Anything over $1B reverts.
contract SpendCap {
    uint256 public constant CAP = 1_000_000_000 * 1e6;
    uint256 public totalSpent;
    function spend(uint256 amountUsd6) external returns (uint256) {
        if (amountUsd6 > CAP) revert("exceeds $1B cap");
        totalSpent += amountUsd6;
        return totalSpent;
    }
}

/// 2. Counter: SSTORE / SLOAD.
contract Counter {
    uint256 public count;
    function increment() external returns (uint256) { count += 1; return count; }
    function add(uint256 n) external returns (uint256) { count += n; return count; }
}

/// 3. Fibonacci (checked arithmetic: fib(n) past 2^256 reverts with Panic 0x11).
contract Fibonacci {
    function fib(uint256 n) external pure returns (uint256) {
        uint256 a = 0;
        uint256 b = 1;
        for (uint256 i = 0; i < n; i++) { (a, b) = (b, a + b); }
        return a;
    }
}

/// 4. Factorial with an overflow check (58! no longer fits in 256 bits).
contract Factorial {
    function fact(uint256 n) external pure returns (uint256 r) {
        r = 1;
        for (uint256 i = 2; i <= n; i++) r *= i;
    }
}

/// 5. GCD (Euclid).
contract GCD {
    function gcd(uint256 a, uint256 b) external pure returns (uint256) {
        while (b != 0) { (a, b) = (b, a % b); }
        return a;
    }
}

/// 6. Prime check (trial division).
contract Prime {
    function isPrime(uint256 n) external pure returns (bool) {
        if (n < 2) return false;
        for (uint256 d = 2; d * d <= n; d++) if (n % d == 0) return false;
        return true;
    }
}

/// 7. Token: balances of 8 accounts by id; a short balance reverts.
contract Token {
    uint256[8] public balance;
    function transfer(uint256 from, uint256 to, uint256 amount) external returns (uint256) {
        require(from < 8 && to < 8, "bad account");
        if (balance[from] < amount) revert("insufficient balance");
        balance[from] -= amount;
        balance[to] += amount;
        return balance[from];
    }
}

/// 8. Vault with a per-withdrawal limit.
contract Vault {
    uint256 public balance;
    uint256 public limit = 500 ether;
    function deposit(uint256 amount) external returns (uint256) { balance += amount; return balance; }
    function withdraw(uint256 amount) external returns (uint256) {
        if (amount > limit) revert("over withdrawal limit");
        if (amount > balance) revert("insufficient funds");
        balance -= amount;
        return balance;
    }
}

/// 9. Voting: 3 candidates, 8 voters, a second vote reverts.
contract Voting {
    uint256[3] public tally;
    bool[8] public voted;
    function vote(uint256 voter, uint256 candidate) external returns (uint256) {
        require(voter < 8 && candidate < 3, "bad input");
        if (voted[voter]) revert("already voted");
        voted[voter] = true;
        tally[candidate] += 1;
        return tally[candidate];
    }
}

/// 10. Escrow: released only when the delivery code matches; otherwise it reverts.
contract Escrow {
    uint256 public amount = 25 ether;
    uint256 public deliveryCode = 424242;
    bool public released;
    function release(uint256 code) external returns (uint256) {
        if (released) revert("already released");
        if (code != deliveryCode) revert("condition not met");
        released = true;
        return amount;
    }
}
