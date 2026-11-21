// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title VehicleLedger
/// @notice Tamper-evident vehicle lifecycle ledger: registration, servicing, accidents,
///         ownership transfers and stolen-status. Only hashes and minimal facts are stored
///         on-chain; full documents stay off-chain and are verified by re-hashing (SHA-256).
/// @dev    Educational prototype. Use synthetic data only. Mirrors app/ledger/contract.py.
contract VehicleLedger {
    // ------------------------------------------------------------------ types
    enum Role { None, Registrar, ServiceCenter, Insurer, Lender }
    enum EventType { Registration, Service, Accident, OwnershipTransfer, StatusChange, LienPlaced, LienReleased }

    struct Vehicle {
        address owner;
        uint32 odometer;      // km, never allowed to decrease
        uint64 registeredAt;
        bool stolen;
        bool exists;
        address lienHolder;   // address(0) when no lien is active
    }

    struct Record {
        EventType eventType;
        address actor;
        uint64 timestamp;
        uint32 odometer;
        bytes32 dataHash;     // SHA-256 of the off-chain JSON document
    }

    // ----------------------------------------------------------------- storage
    address public immutable admin;
    mapping(address => Role) public roles;
    mapping(bytes32 => Vehicle) private vehicles;   // key: SHA-256(VIN)
    mapping(bytes32 => Record[]) private history;

    // ------------------------------------------------------------------ events
    event RoleGranted(address indexed account, Role role);
    event RecordAdded(bytes32 indexed vinHash, uint256 indexed index, EventType eventType,
                      address indexed actor, uint32 odometer, bytes32 dataHash);
    event OwnershipTransferred(bytes32 indexed vinHash, address indexed from, address indexed to);
    event StolenStatusChanged(bytes32 indexed vinHash, bool stolen);
    event LienPlaced(bytes32 indexed vinHash, address indexed lender);
    event LienReleased(bytes32 indexed vinHash, address indexed lender);

    // ------------------------------------------------------------------ errors
    error Unauthorized(string reason);
    error VehicleNotFound();
    error DuplicateVehicle();
    error OdometerRollback(uint32 attempted, uint32 lastRecorded);
    error VehicleStolen();
    error LienActive();
    error NoActiveLien();
    error InvalidOwner();

    // --------------------------------------------------------------- modifiers
    modifier onlyAdmin() {
        if (msg.sender != admin) revert Unauthorized("admin only");
        _;
    }

    modifier onlyRole(Role r) {
        if (msg.sender != admin && roles[msg.sender] != r) revert Unauthorized("missing role");
        _;
    }

    modifier exists(bytes32 vinHash) {
        if (!vehicles[vinHash].exists) revert VehicleNotFound();
        _;
    }

    constructor() {
        admin = msg.sender;
    }

    // ------------------------------------------------------------- administration
    function grantRole(address account, Role role) external onlyAdmin {
        roles[account] = role;
        emit RoleGranted(account, role);
    }

    // -------------------------------------------------------------- state changes
    function registerVehicle(bytes32 vinHash, address owner, uint32 odometer, bytes32 dataHash)
        external onlyRole(Role.Registrar)
    {
        if (vehicles[vinHash].exists) revert DuplicateVehicle();
        if (owner == address(0)) revert InvalidOwner();
        vehicles[vinHash] = Vehicle(owner, odometer, uint64(block.timestamp), false, true, address(0));
        _append(vinHash, EventType.Registration, odometer, dataHash);
    }

    function addServiceRecord(bytes32 vinHash, uint32 odometer, bytes32 dataHash)
        external onlyRole(Role.ServiceCenter) exists(vinHash)
    {
        _updateOdometer(vinHash, odometer);
        _append(vinHash, EventType.Service, odometer, dataHash);
    }

    function reportAccident(bytes32 vinHash, uint32 odometer, bytes32 dataHash)
        external onlyRole(Role.Insurer) exists(vinHash)
    {
        _updateOdometer(vinHash, odometer);
        _append(vinHash, EventType.Accident, odometer, dataHash);
    }

    function transferOwnership(bytes32 vinHash, address newOwner, uint32 odometer, bytes32 dataHash)
        external exists(vinHash)
    {
        Vehicle storage v = vehicles[vinHash];
        if (msg.sender != v.owner) revert Unauthorized("only current owner");
        if (v.stolen) revert VehicleStolen();
        if (v.lienHolder != address(0)) revert LienActive();
        if (newOwner == address(0) || newOwner == msg.sender) revert InvalidOwner();
        _updateOdometer(vinHash, odometer);
        address previous = v.owner;
        v.owner = newOwner;
        _append(vinHash, EventType.OwnershipTransfer, odometer, dataHash);
        emit OwnershipTransferred(vinHash, previous, newOwner);
    }

    function setStolen(bytes32 vinHash, bool stolen, bytes32 dataHash)
        external onlyRole(Role.Registrar) exists(vinHash)
    {
        vehicles[vinHash].stolen = stolen;
        _append(vinHash, EventType.StatusChange, vehicles[vinHash].odometer, dataHash);
        emit StolenStatusChanged(vinHash, stolen);
    }

    function placeLien(bytes32 vinHash, bytes32 dataHash)
        external onlyRole(Role.Lender) exists(vinHash)
    {
        Vehicle storage v = vehicles[vinHash];
        if (v.lienHolder != address(0)) revert LienActive();
        v.lienHolder = msg.sender;
        _append(vinHash, EventType.LienPlaced, v.odometer, dataHash);
        emit LienPlaced(vinHash, msg.sender);
    }

    function releaseLien(bytes32 vinHash, bytes32 dataHash) external exists(vinHash) {
        Vehicle storage v = vehicles[vinHash];
        if (v.lienHolder == address(0)) revert NoActiveLien();
        if (msg.sender != v.lienHolder && msg.sender != admin) revert Unauthorized("only the lien holder");
        address lender = v.lienHolder;
        v.lienHolder = address(0);
        _append(vinHash, EventType.LienReleased, v.odometer, dataHash);
        emit LienReleased(vinHash, lender);
    }

    // ---------------------------------------------------------------------- views
    function getVehicle(bytes32 vinHash)
        external view exists(vinHash)
        returns (address owner, uint32 odometer, bool stolen, uint64 registeredAt, address lienHolder)
    {
        Vehicle storage v = vehicles[vinHash];
        return (v.owner, v.odometer, v.stolen, v.registeredAt, v.lienHolder);
    }

    function getRecordCount(bytes32 vinHash) external view returns (uint256) {
        return history[vinHash].length;
    }

    function getRecord(bytes32 vinHash, uint256 index) external view returns (Record memory) {
        return history[vinHash][index];
    }

    /// @notice Buyer-side check: does this off-chain document hash match what was recorded?
    function verifyRecord(bytes32 vinHash, uint256 index, bytes32 dataHash)
        external view returns (bool)
    {
        return history[vinHash][index].dataHash == dataHash;
    }

    // ------------------------------------------------------------------- internals
    function _updateOdometer(bytes32 vinHash, uint32 odometer) private {
        Vehicle storage v = vehicles[vinHash];
        if (odometer < v.odometer) revert OdometerRollback(odometer, v.odometer);
        v.odometer = odometer;
    }

    function _append(bytes32 vinHash, EventType t, uint32 odometer, bytes32 dataHash) private {
        history[vinHash].push(Record(t, msg.sender, uint64(block.timestamp), odometer, dataHash));
        emit RecordAdded(vinHash, history[vinHash].length - 1, t, msg.sender, odometer, dataHash);
    }
}
