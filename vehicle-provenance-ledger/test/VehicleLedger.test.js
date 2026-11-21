const { expect } = require("chai");
const { ethers } = require("hardhat");

const sha256 = (s) => ethers.sha256(ethers.toUtf8Bytes(s));
const ROLE = { None: 0, Registrar: 1, ServiceCenter: 2, Insurer: 3, Lender: 4 };

describe("VehicleLedger", () => {
  let ledger, admin, registrar, workshop, insurer, lender, asha, rohan, vin;

  beforeEach(async () => {
    [admin, registrar, workshop, insurer, asha, rohan, lender] = await ethers.getSigners();
    ledger = await ethers.deployContract("VehicleLedger");
    await ledger.grantRole(registrar.address, ROLE.Registrar);
    await ledger.grantRole(workshop.address, ROLE.ServiceCenter);
    await ledger.grantRole(insurer.address, ROLE.Insurer);
    await ledger.grantRole(lender.address, ROLE.Lender);
    vin = sha256("MA3ERLF1S00123456");
    await ledger.connect(registrar).registerVehicle(vin, asha.address, 10, sha256("reg-doc"));
  });

  it("registers a vehicle and stores a hash-only history", async () => {
    const [owner, odo, stolen, , lienHolder] = await ledger.getVehicle(vin);
    expect(lienHolder).to.equal(ethers.ZeroAddress);
    expect(owner).to.equal(asha.address);
    expect(odo).to.equal(10);
    expect(stolen).to.equal(false);
    expect(await ledger.getRecordCount(vin)).to.equal(1);
  });

  it("rejects duplicate VINs", async () => {
    await expect(ledger.connect(registrar).registerVehicle(vin, rohan.address, 0, sha256("x")))
      .to.be.revertedWithCustomError(ledger, "DuplicateVehicle");
  });

  it("enforces role-based access", async () => {
    await expect(ledger.connect(insurer).addServiceRecord(vin, 500, sha256("s")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
    await expect(ledger.connect(workshop).reportAccident(vin, 500, sha256("a")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
    await expect(ledger.connect(asha).grantRole(asha.address, ROLE.Registrar))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
  });

  it("blocks odometer rollback", async () => {
    await ledger.connect(workshop).addServiceRecord(vin, 12000, sha256("s1"));
    await expect(ledger.connect(workshop).addServiceRecord(vin, 8000, sha256("s2")))
      .to.be.revertedWithCustomError(ledger, "OdometerRollback").withArgs(8000, 12000);
  });

  it("lets only the current owner transfer, and transfers rights", async () => {
    await expect(ledger.connect(rohan).transferOwnership(vin, rohan.address, 100, sha256("t")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
    await expect(ledger.connect(asha).transferOwnership(vin, rohan.address, 15000, sha256("t")))
      .to.emit(ledger, "OwnershipTransferred").withArgs(vin, asha.address, rohan.address);
    await expect(ledger.connect(asha).transferOwnership(vin, asha.address, 16000, sha256("t2")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
  });

  it("blocks sale of a stolen vehicle until cleared", async () => {
    await ledger.connect(registrar).setStolen(vin, true, sha256("fir"));
    await expect(ledger.connect(asha).transferOwnership(vin, rohan.address, 100, sha256("t")))
      .to.be.revertedWithCustomError(ledger, "VehicleStolen");
    await ledger.connect(registrar).setStolen(vin, false, sha256("recovered"));
    await ledger.connect(asha).transferOwnership(vin, rohan.address, 100, sha256("t"));
  });

  it("verifies off-chain documents by hash", async () => {
    const doc = JSON.stringify({ description: "Oil change", cost_inr: 3500 });
    await ledger.connect(workshop).addServiceRecord(vin, 9000, sha256(doc));
    expect(await ledger.verifyRecord(vin, 1, sha256(doc))).to.equal(true);
    expect(await ledger.verifyRecord(vin, 1, sha256(doc + " edited"))).to.equal(false);
  });

  it("reverts on unknown vehicles", async () => {
    await expect(ledger.getVehicle(sha256("UNKNOWN")))
      .to.be.revertedWithCustomError(ledger, "VehicleNotFound");
  });

  it("blocks transfers while a lien is active and allows them after release", async () => {
    await ledger.connect(lender).placeLien(vin, sha256("loan-1"));
    await expect(ledger.connect(asha).transferOwnership(vin, rohan.address, 100, sha256("t")))
      .to.be.revertedWithCustomError(ledger, "LienActive");
    await expect(ledger.connect(rohan).releaseLien(vin, sha256("r")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
    await ledger.connect(lender).releaseLien(vin, sha256("loan-1-closed"));
    await ledger.connect(asha).transferOwnership(vin, rohan.address, 100, sha256("t"));
  });

  it("only lenders may place a lien, and only one at a time", async () => {
    await expect(ledger.connect(workshop).placeLien(vin, sha256("x")))
      .to.be.revertedWithCustomError(ledger, "Unauthorized");
    await ledger.connect(lender).placeLien(vin, sha256("loan-1"));
    await expect(ledger.connect(lender).placeLien(vin, sha256("loan-2")))
      .to.be.revertedWithCustomError(ledger, "LienActive");
    await expect(ledger.connect(lender).releaseLien(vin, sha256("r")))
      .to.emit(ledger, "LienReleased");
    await expect(ledger.connect(lender).releaseLien(vin, sha256("r2")))
      .to.be.revertedWithCustomError(ledger, "NoActiveLien");
  });
});
