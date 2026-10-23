import type { AccountCard } from "./types";
import { Icon } from "./Icons";
export function AccountCards({ cards }: { cards: AccountCard[] }) {
  return (
    <>
      {cards.map((card, index) => (
        <section
          className="account-card"
          key={index}
          aria-label="From your account"
        >
          <header>
            <span>
              <Icon name="shield" size={17} />
              From your account
            </span>
            <small>
              {card.type === "balances"
                ? "Balances"
                : card.type === "transactions"
                  ? "Recent transactions"
                  : "Cards"}
            </small>
          </header>
          {card.type === "balances" && (
            <div className="balance-list">
              {!card.accounts.length && (
                <p className="empty-card">No accounts found.</p>
              )}
              {card.accounts.map((account, i) => (
                <div className="balance-row" key={i}>
                  <div>
                    <strong>{account.nickname}</strong>
                    <small>
                      {account.type.replaceAll("_", " ")} ·{" "}
                      {account.masked_number}
                    </small>
                  </div>
                  <b>{account.balance}</b>
                </div>
              ))}
            </div>
          )}
          {card.type === "transactions" && (
            <div className="table-scroll">
              {!card.transactions.length ? (
                <p className="empty-card">No matching transactions found.</p>
              ) : (
                <table className="transaction-table">
                  <thead>
                    <tr>
                      <th>Transaction</th>
                      <th>Account</th>
                      <th>Direction</th>
                      <th>Amount</th>
                    </tr>
                  </thead>
                  <tbody>
                    {card.transactions.map((item, i) => (
                      <tr key={i}>
                        <td>
                          <strong>{item.description}</strong>
                          <small title={item.posted_at}>
                            {item.posted_at.slice(0, 10)}
                          </small>
                        </td>
                        <td>{item.masked_number}</td>
                        <td>
                          <span
                            className={`transaction-direction ${item.direction}`}
                          >
                            {item.direction}
                          </span>
                        </td>
                        <td className="amount">{item.amount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
          {card.type === "cards" && (
            <div className="bank-card-list">
              {!card.cards.length && (
                <p className="empty-card">No cards found.</p>
              )}
              {card.cards.map((item, i) => (
                <div className="bank-card-row" key={i}>
                  <span className="mini-bank-card" aria-hidden="true">
                    ••••
                  </span>
                  <div>
                    <strong>
                      {item.kind === "credit" ? "Credit card" : "Debit card"} ·
                      •••• {item.last4}
                    </strong>
                    <small>Expires {item.expiry}</small>
                  </div>
                  <span className={`card-status ${item.status}`}>
                    {item.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </>
  );
}
