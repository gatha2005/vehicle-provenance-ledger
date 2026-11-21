// Deploy to the local Hardhat node and wire up demo roles.
//   npx hardhat node                       (terminal 1)
//   npm run deploy:local                   (terminal 2)
const hre = require("hardhat");

async function main() {
  const [admin, registrar, workshop, insurer, , , lender] = await hre.ethers.getSigners();
  const ledger = await hre.ethers.deployContract("VehicleLedger");
  await ledger.waitForDeployment();
  await (await ledger.grantRole(registrar.address, 1)).wait();  // Registrar
  await (await ledger.grantRole(workshop.address, 2)).wait();   // ServiceCenter
  await (await ledger.grantRole(insurer.address, 3)).wait();    // Insurer
  await (await ledger.grantRole(lender.address, 4)).wait();     // Lender
  console.log("VehicleLedger deployed to:", await ledger.getAddress());
  console.log({ admin: admin.address, registrar: registrar.address,
                workshop: workshop.address, insurer: insurer.address });
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
