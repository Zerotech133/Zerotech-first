package com.qianjiangsoft.graphcore.model;

import cn.hutool.core.text.StrPool;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.engine.CardParamContextHolder;
import com.qianjiangsoft.graphcore.engine.CardQueryParam;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.CardHelper;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.extern.slf4j.Slf4j;

import java.util.*;
import java.util.stream.Collectors;

/**
 * @author marshallwu
 * @date 2022/2/23 16:39
 */

@Data
@AllArgsConstructor
@Slf4j
public class Space {

    private String id;
    private String name;

    /** 点和边原始数据(原始数据) */
    private List<Vertex> vertexList = new ArrayList<>();
    private List<Edge> edgeList = new ArrayList<>();


    /** 点映射 */
    private Map<String, Vertex> vertexMap = new HashMap<>(16);

    /** type映射存储 */
    private Map<EdgeType, Set<Edge>> typeEdgeMap = new HashMap<>();

    /** 邻接表存储 */
    private Map<Vertex, Set<Edge>> adjacencyTableMap = new HashMap<>();

    /** 逆向邻接表存储 */
    private Map<Vertex, Set<Edge>> reverseAdjacencyTableMap = new HashMap<>();

    /** 点统计信息 vertexId + edge type */
    private Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap = new HashMap<>();

    /** 边统计信息 key为source id + dest id + edge type*/
    private Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap = new HashMap<>();

    /**合并边相关*/
    private List<MergeEdge> mergeEdgeList = new ArrayList<>();

    /** 合并边邻接表存储 */
    private Map<Vertex, Set<MergeEdge>> mergeEdgeAdjacencyTableMap = new HashMap<>();

    /** 合并边逆向邻接表存储 */
    private Map<Vertex, Set<MergeEdge>> reverseMergeEdgeAdjacencyTableMap = new HashMap<>();

    /** 合并边按卡片分类邻接表 (key: 卡片Key+卡片value)**/
    private Map<String, Map<Vertex, Set<MergeEdge>>> mergeEdgeAdjacencyTableMapWithCard = new HashMap<>();
    private Map<String, Map<Vertex, Set<MergeEdge>>> reverseMergeEdgeAdjacencyTableMapWithCard = new HashMap<>();

    public Space(String id, String name) {
        this.id = id;
        this.name = name;
    }

    public Map<Vertex, Set<MergeEdge>> getMergeEdgeAdjacencyTableMapByCard(){

        CardQueryParam param = CardParamContextHolder.get();

        if(param != null) {
            log.info("当前cardParam引用:{}", param);
            log.info("查找指定卡片[{}]:[{}]邻接表数据", param.getKey(), StrUtil.join(StrPool.COMMA, param.getValueList()));
            return  param.getValueList().stream()
                    .map(item -> this.mergeEdgeAdjacencyTableMapWithCard.get(CardHelper.getCardAdjacencyTableKey(param.getKey(), item)))
                    .filter(Objects::nonNull)
                    .flatMap(map -> map.entrySet().stream()).collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue,
                            (v1,v2) -> {
                                v1.addAll(v2);
                                return v1;
                            }));
        }
        return this.mergeEdgeAdjacencyTableMap;
    }

    public Map<Vertex, Set<MergeEdge>> getReverseMergeEdgeAdjacencyTableMapByCard(){
        CardQueryParam param = CardParamContextHolder.get();
        if(param != null) {
            log.info("查找指定卡片[{}]:[{}]逆邻接表数据", param.getKey(), StrUtil.join(StrPool.COMMA, param.getValueList()));
            return  param.getValueList().stream()
                    .map(item -> this.reverseMergeEdgeAdjacencyTableMapWithCard.get(CardHelper.getCardAdjacencyTableKey(param.getKey(), item)))
                    .filter(Objects::nonNull)
                    .flatMap(map -> map.entrySet().stream()).collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue,
                            (v1,v2) -> {
                                v1.addAll(v2);
                                return v1;
                            }));
        }
        return this.reverseMergeEdgeAdjacencyTableMap;
    }

    public List<Edge> getEdgeListByCard(){
        CardQueryParam param = CardParamContextHolder.get();
        if(param != null) {
            return this.edgeList.stream().filter(item -> param.getValueList().contains(item.getCardList().get(param.getKey()))).collect(Collectors.toList());
        }else {
            return this.edgeList;
        }
    }
}
