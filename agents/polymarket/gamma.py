import httpx
import json

from agents.polymarket.polymarket import Polymarket
from agents.utils.objects import Market, PolymarketEvent, ClobReward, Tag


class GammaMarketClient:
    def __init__(self):
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.gamma_markets_endpoint = self.gamma_url + "/markets"
        self.gamma_events_endpoint = self.gamma_url + "/events"

    def parse_pydantic_market(self, market_object: dict) -> Market:
        try:
            if "clobRewards" in market_object:
                clob_rewards: list[ClobReward] = []
                for clob_rewards_obj in market_object["clobRewards"]:
                    clob_rewards.append(ClobReward(**clob_rewards_obj))
                market_object["clobRewards"] = clob_rewards

            if "events" in market_object:
                events: list[PolymarketEvent] = []
                for market_event_obj in market_object["events"]:
                    events.append(self.parse_nested_event(market_event_obj))
                market_object["events"] = events

            # These two fields below are returned as stringified lists from the api
            if "outcomePrices" in market_object:
                market_object["outcomePrices"] = json.loads(
                    market_object["outcomePrices"]
                )
            if "clobTokenIds" in market_object:
                market_object["clobTokenIds"] = json.loads(
                    market_object["clobTokenIds"]
                )

            return Market(**market_object)
        except Exception as err:
            print(f"[parse_market] Caught exception: {err}")
            print("exception while handling object:", market_object)

    # Event parser for events nested under a markets api response
    def parse_nested_event(self, event_object: dict()) -> PolymarketEvent:
        print("[parse_nested_event] called with:", event_object)
        try:
            if "tags" in event_object:
                print("tags here", event_object["tags"])
                tags: list[Tag] = []
                for tag in event_object["tags"]:
                    tags.append(Tag(**tag))
                event_object["tags"] = tags

            return PolymarketEvent(**event_object)
        except Exception as err:
            print(f"[parse_event] Caught exception: {err}")
            print("\n", event_object)

    def parse_pydantic_event(self, event_object: dict) -> PolymarketEvent:
        try:
            if "tags" in event_object:
                print("tags here", event_object["tags"])
                tags: list[Tag] = []
                for tag in event_object["tags"]:
                    tags.append(Tag(**tag))
                event_object["tags"] = tags
            return PolymarketEvent(**event_object)
        except Exception as err:
            print(f"[parse_event] Caught exception: {err}")

    def get_markets(
        self, querystring_params={}, parse_pydantic=False, local_file_path=None
    ) -> "list[Market]":
        if parse_pydantic and local_file_path is not None:
            raise Exception(
                'Cannot use "parse_pydantic" and "local_file" params simultaneously.'
            )

        response = httpx.get(self.gamma_markets_endpoint, params=querystring_params)
        if response.status_code == 200:
            data = response.json()
            if local_file_path is not None:
                with open(local_file_path, "w+") as out_file:
                    json.dump(data, out_file)
            elif not parse_pydantic:
                return data
            else:
                markets: list[Market] = []
                for market_object in data:
                    markets.append(self.parse_pydantic_market(market_object))
                return markets
        else:
            print(f"Error response returned from api: HTTP {response.status_code}")
            raise Exception()

    def get_events(
        self, querystring_params={}, parse_pydantic=False, local_file_path=None
    ) -> "list[PolymarketEvent]":
        if parse_pydantic and local_file_path is not None:
            raise Exception(
                'Cannot use "parse_pydantic" and "local_file" params simultaneously.'
            )

        response = httpx.get(self.gamma_events_endpoint, params=querystring_params)
        if response.status_code == 200:
            data = response.json()
            if local_file_path is not None:
                with open(local_file_path, "w+") as out_file:
                    json.dump(data, out_file)
            elif not parse_pydantic:
                return data
            else:
                events: list[PolymarketEvent] = []
                for market_event_obj in data:
                    events.append(self.parse_event(market_event_obj))
                return events
        else:
            raise Exception()

    def get_all_markets(self, limit=2) -> "list[Market]":
        return self.get_markets(querystring_params={"limit": limit})

    def get_all_events(self, limit=2) -> "list[PolymarketEvent]":
        return self.get_events(querystring_params={"limit": limit})

    def get_current_markets(self, limit=4) -> "list[Market]":
        return self.get_markets(
            querystring_params={
                "active": True,
                "closed": False,
                "archived": False,
                "limit": limit,
            }
        )

    def get_all_current_markets(self, limit=100) -> "list[Market]":
        offset = 0
        all_markets = []
        while True:
            params = {
                "active": True,
                "closed": False,
                "archived": False,
                "limit": limit,
                "offset": offset,
            }
            market_batch = self.get_markets(querystring_params=params)
            all_markets.extend(market_batch)

            if len(market_batch) < limit:
                break
            offset += limit

        return all_markets

    def get_current_events(self, limit=4) -> "list[PolymarketEvent]":
        return self.get_events(
            querystring_params={
                "active": True,
                "closed": False,
                "archived": False,
                "limit": limit,
            }
        )

    def get_clob_tradable_markets(self, limit=2) -> "list[Market]":
        return self.get_markets(
            querystring_params={
                "active": True,
                "closed": False,
                "archived": False,
                "limit": limit,
                "enableOrderBook": True,
            }
        )

    def get_market(self, market_id: int) -> dict():
        url = self.gamma_markets_endpoint + "/" + str(market_id)
        print(url)
        response = httpx.get(url)
        return response.json()
    
    def get_live_sports_markets(
        self,
        topics: list = None,
        min_liquidity: float = 0.0,
        max_liquidity: float = None,
        limit: int = 100,
        parse_pydantic: bool = False,
        max_hours_ahead: float = 6.0
    ) -> list:
        """
        Get live sports markets filtered by liquidity and time.
        
        This method accesses markets similar to what appears in Polymarket's
        "live tab" for sports, with liquidity and time filtering applied at the API level.
        
        Args:
            topics: List of sports topics (e.g., ["nfl", "nba", "nhl", "soccer"])
                   If None, queries all sports markets
            min_liquidity: Minimum liquidity threshold (default: 0.0)
            max_liquidity: Maximum liquidity threshold (optional)
            limit: Maximum number of markets to return per topic
            parse_pydantic: Whether to parse results as Pydantic models
            max_hours_ahead: Maximum hours ahead to consider "live" (default: 6.0)
            
        Returns:
            List of market dictionaries (or Market objects if parse_pydantic=True)
        """
        from datetime import datetime, timezone, timedelta
        
        if topics is None:
            topics = ["nfl", "nba", "nhl", "soccer", "sports", "esports"]
        
        all_markets = []
        seen_ids = set()
        
        # Filter for markets ending within max_hours_ahead
        now_utc = datetime.now(timezone.utc)
        max_end_time = now_utc + timedelta(hours=max_hours_ahead)
        end_date_min = now_utc.isoformat()
        end_date_max = max_end_time.isoformat()
        
        for topic in topics:
            params = {
                "topic": topic,
                "active": True,
                "closed": False,
                "archived": False,
                "limit": limit,
                "enableOrderBook": True,  # Only markets with orderbooks
                "end_date_min": end_date_min,  # Markets ending after now
                "end_date_max": end_date_max,  # Markets ending within max_hours_ahead
            }
            
            # Add liquidity filtering if specified
            if min_liquidity > 0.0:
                params["liquidity_num_min"] = min_liquidity
            
            if max_liquidity is not None and max_liquidity > 0.0:
                params["liquidity_num_max"] = max_liquidity
            
            try:
                markets = self.get_markets(
                    querystring_params=params,
                    parse_pydantic=parse_pydantic
                )
                
                # Deduplicate markets (same market might appear in multiple topics)
                for market in markets:
                    market_id = market.get("id") if isinstance(market, dict) else market.id
                    if market_id and market_id not in seen_ids:
                        seen_ids.add(market_id)
                        all_markets.append(market)
            except Exception as e:
                # Log but continue with other topics
                print(f"Error fetching {topic} markets: {e}")
                continue
        
        return all_markets


if __name__ == "__main__":
    gamma = GammaMarketClient()
    market = gamma.get_market("253123")
    poly = Polymarket()
    object = poly.map_api_to_market(market)
