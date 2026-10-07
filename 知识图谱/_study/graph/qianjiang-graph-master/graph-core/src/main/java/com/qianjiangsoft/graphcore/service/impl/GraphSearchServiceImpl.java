package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import com.qianjiangsoft.graphcore.constants.SearchMode;
import com.qianjiangsoft.graphcore.dto.SearchResultDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.GraphAnalyseHelper;
import com.qianjiangsoft.graphcore.helper.ModelHelper;
import com.qianjiangsoft.graphcore.helper.StatisticsInfoHelper;
import com.qianjiangsoft.graphcore.manager.GraphAnalyseManager;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.*;
import com.qianjiangsoft.graphcore.service.GraphSearchService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.*;


/**
 * @author marshallwu
 * @date 2022/4/28 18:09
 */
@Service
public class GraphSearchServiceImpl implements GraphSearchService {

    @Autowired
    GraphAnalyseManager graphAnalyseManager;

    //复杂查询查找实体
    private Map<String, Vertex> searchVertex(ComplexSearchVertexParam vertexParam, List<Vertex> vertexList,
                                             Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap) {
        FilterVertexParam filterVertexParam = new FilterVertexParam();
        filterVertexParam.setKeyword(vertexParam.getNameKeyword());
        List<Vertex> tempVertexList;
        //如果实体类型不为空，就赋值
        if (BeanUtil.isNotEmpty(vertexParam.getTag())) {
            filterVertexParam.setTagName(vertexParam.getTag().getName());
        }

        //如果条件是属性
        if (BeanUtil.isNotEmpty(vertexParam.getPropertyCondition())) {
            filterVertexParam.setPropertyCondition(vertexParam.getPropertyCondition());
        }
        //如果条件是统计值
        if (BeanUtil.isNotEmpty(vertexParam.getStatisticsCondition())) {
            filterVertexParam.setEdgeType(vertexParam.getStatisticsCondition().getEdgeType());
            Condition condition = new Condition();
            condition.setConditionType(vertexParam.getStatisticsCondition().getConditionType());
            condition.setValue(vertexParam.getStatisticsCondition().getValue());
            //根据不同的筛选条件给filterVertexParam设置condition
            switch (vertexParam.getStatisticsCondition().getStatisticsType()) {
                case strength:
                    filterVertexParam.setStrengthCondition(condition);
                    break;
                case count:
                    filterVertexParam.setCountCondition(condition);
                    break;
                case inDegree:
                    filterVertexParam.setInDegreeCondition(condition);
                    break;
                case outDegree:
                    filterVertexParam.setOutDegreeCondition(condition);
                    break;
                case totalDegree:
                    filterVertexParam.setAllDegreeCondition(condition);
                    break;
            }
        }
        //得到过滤后的点
        tempVertexList = graphAnalyseManager.filterVertexListByParam(vertexList, filterVertexParam);

        //如果是排序
        if (BeanUtil.isNotEmpty(vertexParam.getSorting())) {
            EdgeType edgeType = null;
            //如果是统计排序，需要得到edgeType，否则edgeType为空
            if (BeanUtil.isNotEmpty(vertexParam.getStatisticsCondition())) {
                edgeType = vertexParam.getStatisticsCondition().getEdgeType();
            }
            //得到排序过后的点
            tempVertexList = graphAnalyseManager.sortingVertexList(tempVertexList,
                    vertexStatisticsInfoMap, vertexParam.getSorting(), edgeType);
        }
        //将list转化为map
        Map<String, Vertex> vertexMap = new HashMap<>(ModelHelper.convertVertexListToVertexMap(tempVertexList));

        return vertexMap;
    }


    @Override
    public SearchResultDTO complexSearch(ComplexSearchParam searchParam) {
        Space space = SpaceContextHolder.get();
        List<Vertex> vertexList = space.getVertexList();
        Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap = space.getVertexStatisticsInfoMap();
        SearchMode mode = searchParam.getMode();
        SearchResultDTO result = new SearchResultDTO();
        Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap = space.getMergeEdgeStatisticsInfoMap();

        //如果是查询实体，直接通过方法得到结果
        if (mode.equals(SearchMode.vertex)) {
            ComplexSearchVertexParam vertexParam = searchParam.getVertexParam();
            result.setVertexMap(searchVertex(vertexParam, vertexList, vertexStatisticsInfoMap));
        }

        //如果是查询边
        if (mode.equals(SearchMode.all)) {
            ComplexSearchVertexParam vertexParam1 = searchParam.getVertexParam();
            ComplexSearchVertexParam vertexParam2 = searchParam.getVertexParam2();

            Map<EdgeType, Set<Edge>> typeEdgeMap = space.getTypeEdgeMap();
            Set<Edge> edgeSet = typeEdgeMap.get(searchParam.getEdgeParam().getEdgeType());

            FilterEdgeParam filterEdgeParam = new FilterEdgeParam();
            filterEdgeParam.setCountCondition(searchParam.getEdgeParam().getCountCondition());
            filterEdgeParam.setStrengthCondition(searchParam.getEdgeParam().getStrengthCondition());
            filterEdgeParam.setPropertyCondition(searchParam.getEdgeParam().getPropertyCondition());
            filterEdgeParam.setTimeCondition(searchParam.getEdgeParam().getTimeCondition());

            //首先得到满足实体条件的实体
            Map<String, Vertex> vertexMap1 = searchVertex(vertexParam1, vertexList, vertexStatisticsInfoMap);

            Map<String, Vertex> vertexMap2 = searchVertex(vertexParam2, vertexList, vertexStatisticsInfoMap);

            //对筛选排序后的边遍历，如果边的两端实体满足实体的条件，就加入到结果
            for (MergeEdge edge : graphAnalyseManager.filterAndSortingEdgeListByParam(edgeSet,
                    mergeEdgeStatisticsInfoMap,
                    filterEdgeParam, searchParam.getEdgeParam().getSorting())) {
                if (vertexMap1.containsKey(edge.getSource().getId()) && vertexMap2.containsKey(edge.getDestination().getId())) {
                    result.getMergeEdgeList().add(edge);
                    continue;
                }
                if (vertexMap2.containsKey(edge.getSource().getId()) && vertexMap1.containsKey(edge.getDestination().getId())) {
                    result.getMergeEdgeList().add(edge);
                }
            }
        }

        return result;
    }

    @Override
    public SearchResultDTO statisticsSearch(StatisticsSearchParam searchParam) {
        SearchMode mode = searchParam.getMode();
        EdgeType edgeType = searchParam.getEdgeType();
        Condition strengthCondition = searchParam.getStrengthCondition();
        Condition countCondition = searchParam.getCountCondition();
        SortingParam sortingParam = searchParam.getSorting();

        Space space = SpaceContextHolder.get();
        Map<String, VertexStatisticsInfo> vertexStatisticsInfoMap = space.getVertexStatisticsInfoMap();
        Map<String, MergeEdgeStatisticsInfo> mergeEdgeStatisticsInfoMap = space.getMergeEdgeStatisticsInfoMap();


        List<Vertex> vertexList = space.getVertexList();
        SearchResultDTO result = new SearchResultDTO();
        Map<EdgeType, Set<Edge>> typeEdgeMap = space.getTypeEdgeMap();

        List<Vertex> tempVertexList;
        List<MergeEdge> tempEdgeList;

        if (mode.equals(SearchMode.vertex)) {

            //按条件过滤实体
            FilterVertexParam filterVertexParam = new FilterVertexParam();
            filterVertexParam.setEdgeType(edgeType);
            filterVertexParam.setStrengthCondition(strengthCondition);
            filterVertexParam.setCountCondition(countCondition);
            tempVertexList = graphAnalyseManager.filterVertexListByParam(vertexList,
                    filterVertexParam);

            //统计信息
            tempVertexList.stream().forEach(vertex -> {
                vertex.setVertexStatisticsInfo(vertexStatisticsInfoMap.get(StatisticsInfoHelper.getVertexStatisticsInfoKey(vertex.getId(), edgeType.getName())));
            });

            //排序
            if (searchParam.getSorting() != null) {
                //对点进行排序得到有序序列
                List<Vertex> vertexSortList = graphAnalyseManager.sortingVertexList(tempVertexList,
                        vertexStatisticsInfoMap,
                        searchParam.getSorting(), edgeType);

                result.setVertexMap(ModelHelper.convertVertexListToVertexMap(vertexSortList));
            } else {
                result.setVertexMap(ModelHelper.convertVertexListToVertexMap(tempVertexList));
            }
        } else if (mode.equals(SearchMode.edge)) {

            //边的强度条件和次数条件都为空的情况
            Set<Edge> edgeSet = typeEdgeMap.get(edgeType);

            //过滤排名
            FilterEdgeParam filterEdgeParam = new FilterEdgeParam();
            filterEdgeParam.setStrengthCondition(strengthCondition);
            filterEdgeParam.setCountCondition(countCondition);
            tempEdgeList = graphAnalyseManager.filterAndSortingEdgeListByParam(edgeSet, mergeEdgeStatisticsInfoMap,
                    filterEdgeParam, sortingParam);
            result.getMergeEdgeList().addAll(tempEdgeList);
        }
        return result;
    }


    @Override
    public SearchResultDTO textSearch(TextSearchParam searchParam) {

        SearchMode mode = searchParam.getMode();
        Space space = SpaceContextHolder.get();

        List<Vertex> vertexList = space.getVertexList();
        List<Edge> edgeList = space.getEdgeListByCard();

        SearchResultDTO result = new SearchResultDTO();

        String keyword = searchParam.getKeyword();
        if (mode.equals(SearchMode.vertex) || mode.equals(SearchMode.all)) {
            for (Vertex vertex : vertexList) {
                //检查标签
                if (vertex.getName().contains(keyword)) {
                    result.getVertexMap().put(vertex.getId(), vertex);
                }

                //检查属性
                if(GraphAnalyseHelper.checkPropertyValueKeyword(vertex.getPropertyList(), keyword)) {
                    result.getVertexMap().put(vertex.getId(), vertex);
                }
            }

        }
        if (mode.equals(SearchMode.edge) || mode.equals(SearchMode.all)) {

            List<Edge> resultEdgeList = new ArrayList<>();
            for (Edge edge : edgeList) {

                //检查属性
                if(GraphAnalyseHelper.checkPropertyValueKeyword(edge.getPropertyList(), keyword)) {
                    resultEdgeList.add(edge);
                }
            }

            //转换成合并边
            result.setMergeEdgeList(ModelHelper.convertToMergeEdgeList(resultEdgeList, space.getMergeEdgeStatisticsInfoMap()));
        }

        return result;
    }
}

