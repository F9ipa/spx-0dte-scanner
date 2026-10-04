const $ = (id) =>
    document.getElementById(id);


function number(value, decimals = 2) {

    if (
        value === null ||
        value === undefined ||
        isNaN(value)
    ) {

        return "—";

    }

    return Number(value).toFixed(
        decimals
    );
}


function scoreClass(score) {

    if (score >= 80)
        return "good";

    if (score >= 65)
        return "medium";

    return "low";
}


function renderBest(
    element,
    contract,
    label
) {

    if (!contract) {

        element.innerHTML =
            `<div>${label}</div>
             <div class="strike">—</div>
             <div class="meta">
             بانتظار بيانات السلسلة
             </div>`;

        return;
    }


    element.innerHTML = `

        <div>
            ${label}
        </div>

        <div class="strike">
            ${contract.strike}
        </div>

        <div class="meta">

            Score:
            <b>
                ${number(contract.score,0)}
            </b>

            • Delta:
            ${number(contract.delta,2)}

            <br>

            Bid / Ask:

            ${number(contract.bid)}
            /
            ${number(contract.ask)}

            <br>

            Spread:

            ${number(contract.spread,1)}%

        </div>

    `;
}


function renderContracts(
    element,
    contracts
) {

    if (
        !contracts ||
        contracts.length === 0
    ) {

        element.innerHTML =
            `<div class="contract">
             بانتظار بيانات العقود...
             </div>`;

        return;
    }


    element.innerHTML =
        contracts.map(
            contract => `

            <div class="contract">

                <div class="strike">
                    ${contract.strike}
                </div>

                <div>
                    Δ
                    ${number(
                        contract.delta,
                        2
                    )}
                </div>

                <div>
                    ${number(
                        contract.bid
                    )}
                    /
                    ${number(
                        contract.ask
                    )}
                </div>

                <div>
                    ${number(
                        contract.spread,
                        1
                    )}%
                </div>

                <div
                    class="score
                    ${scoreClass(
                        contract.score
                    )}"
                >
                    ${number(
                        contract.score,
                        0
                    )}
                </div>

            </div>

        `
        ).join("");
}


async function update() {

    try {

        const response =
            await fetch(
                "/api/state",
                {
                    cache: "no-store"
                }
            );


        const data =
            await response.json();


        // ---------------------------------------------
        // Market
        // ---------------------------------------------

        $("spx").textContent =
            number(
                data.spx
            );


        $("vix").textContent =
            number(
                data.vix
            );


        $("expected").textContent =
            data.expected_move === null
                ? "—"
                : "±" +
                  number(
                      data.expected_move
                  );


        $("bias").textContent =
            data.status || "—";


        // ---------------------------------------------
        // Connection
        // ---------------------------------------------

        const connection =
            $("connection");


        if (data.connected) {

            connection.textContent =
                "● LIVE";

            connection.className =
                "online";

        } else {

            connection.textContent =
                "● OFFLINE";

            connection.className =
                "offline";

        }


        // ---------------------------------------------
        // Best contracts
        // ---------------------------------------------

        renderBest(

            $("bestCall"),

            data.best_call,

            "🔥 BEST CALL"

        );


        renderBest(

            $("bestPut"),

            data.best_put,

            "🔻 BEST PUT"

        );


        // ---------------------------------------------
        // Top 3
        // ---------------------------------------------

        renderContracts(

            $("calls"),

            data.calls

        );


        renderContracts(

            $("puts"),

            data.puts

        );


        // ---------------------------------------------
        // Update age
        // ---------------------------------------------

        $("age").textContent =
            data.age === null
                ? "—"
                : data.age + "s";


        $("error").textContent =
            data.error || "";

    }

    catch (error) {

        $("error").textContent =
            error.message;

    }

}


update();

setInterval(
    update,
    1000
);
