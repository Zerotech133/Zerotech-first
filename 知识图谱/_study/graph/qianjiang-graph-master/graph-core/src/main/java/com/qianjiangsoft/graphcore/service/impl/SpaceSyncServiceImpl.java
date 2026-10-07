package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.text.StrPool;
import cn.hutool.core.util.NumberUtil;
import cn.hutool.core.util.ObjectUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.helper.CardHelper;
import com.qianjiangsoft.graphcore.helper.ModelHelper;
import com.qianjiangsoft.graphcore.helper.StatisticsInfoHelper;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.service.SpaceCardService;
import com.qianjiangsoft.graphcore.service.SpaceService;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.*;
import java.util.stream.Collectors;

/**
 * @author marshallwu
 * @date 2022/3/4 9:39
 */
@Service
@Slf4j
public class SpaceSyncServiceImpl implements SpaceSyncService {

    @Autowired
    SpaceService spaceService;

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SpaceCardService spaceCardService;


    @Override
    public void syncSpaceInfo(String spaceId) {

        log.info("开始同步空间[{}]信息", spaceId);

        //更新vertex map, mergeEdge列表
        this.updateMergeEdgeList(spaceId);

        //同步邻接表信息
        this.updateAdjacencyMap(spaceId);

        //更新统计数据
        this.updateBaseStatisticsInfo(spaceId);

        log.info("完成同步空间[{}]信息", spaceId);
    }


    @Override
    public void updateBaseStatisticsInfo(String spaceId) {
        Space space = graphEngine.getSpaceMap().get(spaceId);

        //合并边统计数据
        Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap = space.getMergeEdgeStatisticsInfoMap();

        //清空数据
        mergeEdgeStatisticsInfoMap.clear();

        //强度
        Map<String, Double> mergeEdgeStrengthMap = space.getEdgeList().stream().collect(Collectors.groupingBy(StatisticsInfoHelper::getMergeEdgeKey,
                Collectors.summingDouble(Edge::getStrength)));
        for(Map.Entry<String, Double> entry : mergeEdgeStrengthMap.entrySet()) {
            MergeEdgeStatisticsInfo mergeEdgeStatisticsInfo = new MergeEdgeStatisticsInfo();
            mergeEdgeStatisticsInfo.setStrength(entry.getValue());
            mergeEdgeStatisticsInfoMap.put(entry.getKey(), mergeEdgeStatisticsInfo);
        }

        //次数
        Map<String, Long> mergeEdgeCountMap = space.getEdgeList().stream().collect(Collectors.groupingBy(StatisticsInfoHelper::getMergeEdgeKey,
                Collectors.counting()));
        for(Map.Entry<String, Long> entry : mergeEdgeCountMap.entrySet()) {
            mergeEdgeStatisticsInfoMap.get(entry.getKey()).setCount(entry.getValue());
        }


        //点统计数据
        Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap = space.getVertexStatisticsInfoMap();
        Map<String, List<Edge>> vertexInEdgeMap = new HashMap<>();
        Map<String, List<Edge>> vertexOutEdgeMap = new HashMap<>();

        //清空数据
        vertexStatisticsInfoMap.clear();

        //遍历边,建立点的出入边映射
        for(Edge edge : space.getEdgeList()) {
            //入
            String destinationId = edge.getDestination().getId();
            if(!vertexInEdgeMap.containsKey(destinationId)) {
                vertexInEdgeMap.put(destinationId, new ArrayList<>());
            }
            vertexInEdgeMap.get(destinationId).add(edge);


            //出
            String sourceId = edge.getSource().getId();
            if(!vertexOutEdgeMap.containsKey(sourceId)) {
                vertexOutEdgeMap.put(sourceId, new ArrayList<>());
            }
            vertexOutEdgeMap.get(sourceId).add(edge);
        }

        //入方向
        computeVertexInStatistics(vertexInEdgeMap, vertexStatisticsInfoMap);

        //出方向
        computeVertexOutStatistics(vertexOutEdgeMap, vertexStatisticsInfoMap);

        //计算合并值
        for(VertexStatisticsInfo value : vertexStatisticsInfoMap.values()) {
            //度数
            value.setAllDegree(NumberUtil.add(value.getInDegree(), value.getOutDegree()).longValue());

            //强度
            value.setAllStrength(NumberUtil.add(value.getInStrength(), value.getOutStrength()));

            //次数
            value.setAllCount(NumberUtil.add(value.getInCount(), value.getOutCount()).longValue());
        }

    }

    private void computeVertexInStatistics(Map<String, List<Edge>> vertexInEdgeMap, Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap) {
        //入
        for(Map.Entry<String, List<Edge>> entry: vertexInEdgeMap.entrySet()) {

            //度
            Map<String, Long> inDegreeMap = entry.getValue().stream().collect(Collectors
                    .collectingAndThen(
                            Collectors.toCollection(() -> new TreeSet<>(Comparator.comparing(edge -> edge.getSource().getId()))),
                            ArrayList::new)).stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.counting()));

            for(String edgeTypeName: inDegreeMap.keySet()) {
                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setInDegree(inDegreeMap.get(edgeTypeName));
            }

            //次数
            Map<String, Long> inTimeMap = entry.getValue().stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.counting()));

            for(String edgeTypeName: inTimeMap.keySet()) {

                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setInCount(inTimeMap.get(edgeTypeName));
            }


            //强度
            Map<String, Double> inStrengthMap = entry.getValue().stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.summingDouble(Edge::getStrength)));
            for(String edgeTypeName: inStrengthMap.keySet()) {

                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setInStrength(inStrengthMap.get(edgeTypeName));
            }
        }
    }

    private void computeVertexOutStatistics(Map<String, List<Edge>> vertexOutEdgeMap, Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap) {
        for(Map.Entry<String, List<Edge>> entry: vertexOutEdgeMap.entrySet()) {

            //度
            Map<String, Long> outDegreeMap = entry.getValue().stream().collect(Collectors
                    .collectingAndThen(
                            Collectors.toCollection(() -> new TreeSet<>(Comparator.comparing(edge -> edge.getDestination().getId()))),
                            ArrayList::new)).stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.counting()));

            for(String edgeTypeName: outDegreeMap.keySet()) {
                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setOutDegree(outDegreeMap.get(edgeTypeName));
            }


            //次数
            Map<String, Long> outTimeMap = entry.getValue().stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.counting()));
            for(String edgeTypeName: outTimeMap.keySet()) {
                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setOutCount(outTimeMap.get(edgeTypeName));
            }


            //强度
            Map<String, Double> outStrengthMap = entry.getValue().stream().collect(Collectors.
                    groupingBy(edge -> edge.getType().getName(), Collectors.summingDouble(Edge::getStrength)));
            for(String edgeTypeName: outStrengthMap.keySet()) {
                //取出统计对象
                VertexStatisticsInfo vertexStatisticsInfo = StatisticsInfoHelper.getVertexStatisticsInfo(entry.getKey(),
                        edgeTypeName, vertexStatisticsInfoMap);

                vertexStatisticsInfo.setOutStrength(outStrengthMap.get(edgeTypeName));
            }
        }
    }

    @Override
    public void updateAdjacencyMap(String spaceId) {
        Space space = graphEngine.getSpaceMap().get(spaceId);

        List<Edge> edgeList = space.getEdgeList();

        //合并边邻接表
        List<MergeEdge> mergeEdgeList = space.getMergeEdgeList();
        Map<Vertex, Set<MergeEdge>> mergeEdgeAdjacencyTableMap = mergeEdgeList.stream().collect(
                Collectors.groupingBy(MergeEdge::getSource, Collectors.toSet()));
        space.setMergeEdgeAdjacencyTableMap(mergeEdgeAdjacencyTableMap);

        Map<Vertex, Set<MergeEdge>> reverseMergeEdgeAdjacencyTableMap = mergeEdgeList.stream().collect(
                Collectors.groupingBy(MergeEdge::getDestination, Collectors.toSet()));
        space.setReverseMergeEdgeAdjacencyTableMap(reverseMergeEdgeAdjacencyTableMap);


        //分卡片合并边邻接表
        //查询card分类
        List<Card> cardList = spaceCardService.selectCardListBySpaceId(spaceId);
        for (Card card : cardList) {

            List<MergeEdge> filteredMergeEdgeList = mergeEdgeList.stream()
                    .filter(item-> ObjectUtil.isNotNull(item.getCardList().get(card.getKey()))).collect(Collectors.toList());

            Map<String, Map<Vertex, Set<MergeEdge>>> mergeEdgeAdjacencyTableMapWithCard = filteredMergeEdgeList.stream()
                    .collect(Collectors.groupingBy(item ->
                    CardHelper.getCardAdjacencyTableKey(card.getKey(), item.getCardList().get(card.getKey())),
                    Collectors.groupingBy(MergeEdge::getSource, Collectors.toSet())));
            log.info("生成卡片合并边邻接表信息[{}]", StrUtil.join(StrPool.COMMA, mergeEdgeAdjacencyTableMapWithCard.keySet()));
            space.setMergeEdgeAdjacencyTableMapWithCard(mergeEdgeAdjacencyTableMapWithCard);

            Map<String, Map<Vertex, Set<MergeEdge>>> reverseMergeEdgeAdjacencyTableMapWithCard = filteredMergeEdgeList.stream()
                    .collect(Collectors.groupingBy(item ->
                    CardHelper.getCardAdjacencyTableKey(card.getKey(), item.getCardList().get(card.getKey())),
                    Collectors.groupingBy(MergeEdge::getDestination, Collectors.toSet())));
            log.info("生成卡片合并边逆邻接表信息[{}]", StrUtil.join(StrPool.COMMA, reverseMergeEdgeAdjacencyTableMapWithCard.keySet()));
            space.setReverseMergeEdgeAdjacencyTableMapWithCard(reverseMergeEdgeAdjacencyTableMapWithCard);
        }

        //分类索引边
        Map<EdgeType, Set<Edge>> typeEdgeMap = edgeList.stream().collect(
                Collectors.groupingBy(Edge::getType, Collectors.toSet()));
        space.setTypeEdgeMap(typeEdgeMap);
    }

    @Override
    public void updateMergeEdgeList(String spaceId) {

        Space space = graphEngine.getSpaceMap().get(spaceId);

        //更新MergeEdgeList
        space.setMergeEdgeList(ModelHelper.computeMergeEdgeList(space.getEdgeList()));
    }
}
