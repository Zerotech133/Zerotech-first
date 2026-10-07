package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollectionUtil;
import cn.hutool.core.date.DateUnit;
import cn.hutool.core.date.DateUtil;
import com.qianjiangsoft.graphcore.constants.SearchPathDirection;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.ModelHelper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.param.SearchAllPathParam;
import com.qianjiangsoft.graphcore.param.SearchLoopPathParam;
import com.qianjiangsoft.graphcore.param.SearchSameTagPathParam;
import com.qianjiangsoft.graphcore.param.SearchShortestPathParam;
import com.qianjiangsoft.graphcore.service.GraphPathAnalyseService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * @author marshallwu
 * @date 2022/4/28 11:47
 */
@Service
@Slf4j
public class GraphPathAnalyseServiceImpl implements GraphPathAnalyseService {

    @Override
    public List<List<MergeEdge>> searchLoopPath(SearchLoopPathParam searchLoopPathParam) {
        //深度优先搜索
        //定义返回列表
        List<List<MergeEdge>> edgeList = new ArrayList<>();
        Space space = SpaceContextHolder.get();
        if (searchLoopPathParam.getMaxStep() < 2) {
            return edgeList;
        }

        //如果用户没有选点，则进入此处
        if (searchLoopPathParam.getPointList().size() == 0) {
            //查询所有回路
            searchAllLoop(searchLoopPathParam, edgeList);
            return edgeList;
        } else {//如果用户选择了实体，则进入这里

            //暂定一个起点，如果有环的话是一圈，假定从哪开始都一样
            Vertex startPoint = searchLoopPathParam.getPointList().get(0);
            HashSet<Vertex> skipVertex = new HashSet<Vertex>(); //迭代中发现某个点不包含需要的边则直接跳过
            switch (searchLoopPathParam.getDirection()) {
                case forward:
                    firstCaculate(startPoint, edgeList, space.getMergeEdgeAdjacencyTableMapByCard(), searchLoopPathParam, skipVertex);
                    break;
                //无向时合并Map
                case undirected:
                    //获取邻接表总表
                    Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();
                    firstCaculate(startPoint, edgeList, adjacencyEdgeMap, searchLoopPathParam, skipVertex);
                    break;
            }

            return edgeList;
        }

    }

    /**
     * 第一轮迭代
     *
     * @param startPoint
     * @param edgeList
     * @param adjacencyEdgeMap
     * @param searchLoopPathParam
     * @param skipVertex
     */
    private void firstCaculate(Vertex startPoint, List<List<MergeEdge>> edgeList, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, SearchLoopPathParam searchLoopPathParam, HashSet<Vertex> skipVertex) {

        //将边类型储存方便查询
        Set<EdgeType> typeMap = new HashSet<>(searchLoopPathParam.getTypeList());

        Set<MergeEdge> visitedEdge = new HashSet<>();
        //用户指定的实体列表
        Set<Vertex> visitedVertex = new HashSet<>(searchLoopPathParam.getPointList());
        //存入临时路径
        Deque<MergeEdge> tmpList = new LinkedList<>();

        //下一步的边的集合
        Set<MergeEdge> nextEdgeList = adjacencyEdgeMap.get(startPoint);
        if (CollectionUtil.isEmpty(nextEdgeList)) {
            return;
        }
        //判断该点是否只有一条边,不放进skipVertex，因为该点已被作为起始点了
        if (nextEdgeList.size() == 1) {
//            log.info(" the Node:"+startPoint.getName()+" edge only one");
            skipVertex.add(startPoint);
            return;
        }
        //遍历所有边, 然后回溯
        boolean hasRightEdge = false;  //是否有符合条件的边
        //遍历所有边, 然后回溯
        for (MergeEdge nextEdge : nextEdgeList) {
            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
                hasRightEdge = true;
                tmpList.offerLast(nextEdge);
                visitedEdge.add(nextEdge);
                visitedVertex.remove(startPoint);
                findNextNodeToLoop(startPoint, startPoint, visitedVertex, nextEdge, tmpList, edgeList, adjacencyEdgeMap, searchLoopPathParam, visitedEdge, skipVertex);
                visitedEdge.remove(nextEdge);
                tmpList.pollLast();
                visitedVertex.add(startPoint);
            }
        }
        //该点没有符合要求的边
        if (!hasRightEdge) {
//            log.info(" the Node:"+startPoint.getName()+" has no correct type edge!");
            skipVertex.add(startPoint);

        }
    }

    /**
     * 查找本条路径的下一个点
     *
     * @param startPoint          边的起点
     * @param lastVertex          边的终点
     * @param visitedVertex       用户选择的实体列表，每次迭代会做移除
     * @param startEdge           上一步前迭代的边
     * @param tmpList             临时路径
     * @param edgeList            结果路径集
     * @param adjacencyEdgeMap    邻接表  可能是邻接表，也可能是邻接表和逆邻接表的组合（无向时）
     * @param searchLoopPathParam 参数列表
     * @param visitedEdge         已访问过的边
     * @param skipVertex          不用访问的点
     */
    private void findNextNodeToLoop(Vertex startPoint, Vertex lastVertex, Set<Vertex> visitedVertex, MergeEdge startEdge, Deque<MergeEdge> tmpList, List<List<MergeEdge>> edgeList, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, SearchLoopPathParam searchLoopPathParam, Set<MergeEdge> visitedEdge, HashSet<Vertex> skipVertex) {

        //将边类型储存方便查询
        Set<EdgeType> typeMap = new HashSet<>(searchLoopPathParam.getTypeList());
        long maxStep = searchLoopPathParam.getMaxStep();
        //下一个点
        Vertex destNode;
        Set<MergeEdge> nextEdgeList;
        //根据方向取出下个节点
        boolean removeFlag = false;

        switch (searchLoopPathParam.getDirection()) {
            case forward:
                //通过边获取对端节点
                destNode = startEdge.getDestination();
                caculateLoopWithVertex(startPoint, destNode, visitedVertex, tmpList, edgeList, adjacencyEdgeMap, searchLoopPathParam, visitedEdge, skipVertex);
                break;
            //无向
            case undirected:
                //startEdge可能是顺向或逆向，如果是顺向的则取线段的对端，如果是逆向的，则取源端
                destNode = startEdge.getSource().equals(lastVertex) ? startEdge.getDestination() : startEdge.getSource();
                caculateLoopWithVertex(startPoint, destNode, visitedVertex, tmpList, edgeList, adjacencyEdgeMap, searchLoopPathParam, visitedEdge, skipVertex);
                break;
        }
    }

    /**
     * 正式计算路径
     *
     * @param startPoint
     * @param destNode
     * @param visitedVertex
     * @param tmpList
     * @param edgeList
     * @param adjacencyEdgeMap
     * @param searchLoopPathParam
     * @param visitedEdge
     * @param skipVertex
     */
    private void caculateLoopWithVertex(Vertex startPoint, Vertex destNode, Set<Vertex> visitedVertex, Deque<MergeEdge> tmpList, List<List<MergeEdge>> edgeList, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, SearchLoopPathParam searchLoopPathParam, Set<MergeEdge> visitedEdge, HashSet<Vertex> skipVertex) {

        Set<EdgeType> typeMap = new HashSet<>(searchLoopPathParam.getTypeList());
        long maxStep = searchLoopPathParam.getMaxStep();  //最大步数
        //根据方向取出下个节点
        boolean removeFlag = false;
        //开始判断
        //如果destNode中确定没有符合要求的边则直接跳过/超过最大步数返回
        if (skipVertex.contains(destNode) || tmpList.size() > maxStep) {
            return;
        }

        //用户选择的点都遍历到了，且起点和终点相同，加入edgeList并返回
        if (visitedVertex.size() == 0 && destNode.equals(startPoint)) {
            edgeList.add(new LinkedList<>(tmpList));
            return;
        }

        if (!adjacencyEdgeMap.containsKey(destNode)) {
            return;
        }
        //取出以destNode为起点的边，即对端与对端的对端的边
        Set<MergeEdge> nextEdgeList = adjacencyEdgeMap.get(destNode);
        if (CollectionUtil.isEmpty(nextEdgeList)) {
            return;
        }

        //如果是叶子节点，就别费力气了
        if (nextEdgeList.size() == 1) {
//            log.info(" the Node:"+destNode.getName()+" edge only one");
            skipVertex.add(destNode);
        }
        boolean hasRightEdge = false;  //是否有符合条件的边
        //递归入口
        for (MergeEdge nextEdge : nextEdgeList) {
            //如果边没有被访问过，且在搜索的type中，传入递归
            //如果有符合条件的边，则放过，如果一直没有符合条件的边，则该节点直接废弃
            if (typeMap.contains(nextEdge.getType())) {
                //符合参数中边的类型，且该边未被访问过
                if (!visitedEdge.contains(nextEdge)) {
                    hasRightEdge = true;
                    tmpList.offerLast(nextEdge);
                    visitedEdge.add(nextEdge);
                    if (visitedVertex.contains(destNode)) {
                        visitedVertex.remove(destNode);
                        //removeflag用于记录是否移除pointlist里的点
                        removeFlag = true;
                    }
                    findNextNodeToLoop(startPoint, destNode, visitedVertex, nextEdge, tmpList, edgeList, adjacencyEdgeMap, searchLoopPathParam, visitedEdge, skipVertex);
                    visitedEdge.remove(nextEdge);
                    tmpList.pollLast();
                    if (removeFlag) {
                        visitedVertex.add(destNode);
                    }
                }
            }
        }
        if (!hasRightEdge)//如果没有符合类型的边，则该点直接不用再作为起点遍历
        {
//            log.info(" the Node:"+destNode.getName()+" has no correct type edge!");
            skipVertex.add(destNode);
        }
    }


    private void searchAllLoop(SearchLoopPathParam searchLoopPathParam, List<List<MergeEdge>> edgeList) {
        Space space = SpaceContextHolder.get();

        //暂定一个起点，如果有环的话是一圈，假定从哪开始都一样
        List<Vertex> allVertex = space.getVertexList();
        HashSet<Vertex> skipVertex = new HashSet<Vertex>(); //迭代中发现某个点不包含需要的边则直接跳过

        for (Vertex startPoint : allVertex) {
            //判断该实体是否有该类型的边
            if (!skipVertex.contains(startPoint)) {
                switch (searchLoopPathParam.getDirection()) {
                    case forward:
                        firstCaculate(startPoint, edgeList, space.getMergeEdgeAdjacencyTableMapByCard(), searchLoopPathParam, skipVertex);
                        break;
                    //无向时合并Map
                    case undirected:
                        //获取邻接总表
                        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();
                        firstCaculate(startPoint, edgeList, adjacencyEdgeMap, searchLoopPathParam, skipVertex);
                        break;
                }
            }
//            else{
//                log.info("skip the node:"+startPoint.getName());
//            }

        }
    }

    @Override
    /**
     * 查找路径
     */
    public List<List<MergeEdge>> searchAllPath(SearchAllPathParam searchAllPathParam) {

        //深度优先搜索
        //定义返回列表
        List<List<MergeEdge>> edgeList = new ArrayList<>();
        Date startTime=DateUtil.date();
        //判断入参是否符合条件
        if (searchAllPathParam.getSource() == null || searchAllPathParam.getDestination() == null || searchAllPathParam.getSource() == searchAllPathParam.getDestination() || searchAllPathParam.getMaxStep() == 0) {
            return edgeList;
        }
        Space space = SpaceContextHolder.get();
        //定义不同路径方向的邻接表
        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = space.getMergeEdgeAdjacencyTableMapByCard();
        Map<Vertex, Set<MergeEdge>> reverseAdjacencyEdgeMap = space.getReverseMergeEdgeAdjacencyTableMapByCard();
        Map<Vertex, Set<MergeEdge>> alladjacencyEdgeMap= ModelHelper.getAdjacencyEdgeMapWithoutDirection();
        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMapForsource;
        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMapFordestinate;
        //下一步的边集
        Set<MergeEdge> nextEdgeListForSource=new HashSet<>();   //起点的第一层边集
        Set<MergeEdge> nextEdgeListForDestinate=new HashSet<>();//终点的第一层边集
        //点和路径集合的map
        HashMap<Vertex,List<List<MergeEdge>>> VertexRoadMapForSource=new HashMap<>();
        HashMap<Vertex,List<List<MergeEdge>>> VertexRoadMapForDestinate=new HashMap<>();
        //计算两端节点向外扩多少步
        long destinateStep= searchAllPathParam.getMaxStep()/2;
        long sourceStep=searchAllPathParam.getMaxStep()-destinateStep;
        log.info("the sourceStep: "+sourceStep+"; the destinateStep is:"+destinateStep);
        //判断边是否被访问过
        Set<MergeEdge> visitedEdge = new HashSet<>();
        //每个节点最小路径，比他大的直接停止
        HashMap<Vertex,Long> vertexInStep =new HashMap<Vertex, Long>();
        //将边类型储存方便查询，边类型存储到一个HashSet中
        Set<EdgeType> typeMap = new HashSet<>(searchAllPathParam.getEdgeTypeList());
        //存入临时路径，deque是双端队列
        Deque<MergeEdge> tmpList = new LinkedList<>();
        switch (searchAllPathParam.getDirection()) {
            case forward:
               //判断是否有以source为起点的边
                if (!adjacencyEdgeMap.containsKey(searchAllPathParam.getSource())||!reverseAdjacencyEdgeMap.containsKey(searchAllPathParam.getDestination()) ) {
                    return edgeList;
                }
                //取出以source为起点的边
                nextEdgeListForSource = adjacencyEdgeMap.get(searchAllPathParam.getSource());
                adjacencyEdgeMapForsource=adjacencyEdgeMap;
                nextEdgeListForDestinate =reverseAdjacencyEdgeMap.get(searchAllPathParam.getDestination());
                adjacencyEdgeMapFordestinate=reverseAdjacencyEdgeMap;
                break;
            case backward:
                if (!reverseAdjacencyEdgeMap.containsKey(searchAllPathParam.getSource())||!adjacencyEdgeMap.containsKey(searchAllPathParam.getDestination())) {
                    return edgeList;
                }
                //取出以source为起点的边
                nextEdgeListForSource = reverseAdjacencyEdgeMap.get(searchAllPathParam.getSource());
                adjacencyEdgeMapForsource=reverseAdjacencyEdgeMap;
                nextEdgeListForDestinate =adjacencyEdgeMap.get(searchAllPathParam.getDestination());
                adjacencyEdgeMapFordestinate=adjacencyEdgeMap;
                break;

            //无向时合并Map
            default:
                if (!alladjacencyEdgeMap.containsKey(searchAllPathParam.getSource())||!alladjacencyEdgeMap.containsKey(searchAllPathParam.getDestination())) {
                    return edgeList;
                }
                //取出以source为起点的边
                nextEdgeListForSource = alladjacencyEdgeMap.get(searchAllPathParam.getSource());
                nextEdgeListForDestinate =alladjacencyEdgeMap.get(searchAllPathParam.getDestination());
                adjacencyEdgeMapForsource=alladjacencyEdgeMap;
                adjacencyEdgeMapFordestinate=alladjacencyEdgeMap;
        }
        if (CollectionUtil.isEmpty(nextEdgeListForSource)||CollectionUtil.isEmpty(nextEdgeListForDestinate)) {
            return edgeList;
        }
        //先遍历起点所有边, 如果找到终点，存结果，如果没有找到终点则存点-路径（数组）
        for (MergeEdge nextEdge : nextEdgeListForSource) {
            //如果边类型包括
            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
                //添加在队尾
                tmpList.offerLast(nextEdge);
                //已访问加入
                visitedEdge.add(nextEdge);
//                log.info("findPath (source):"+nextEdge.getSource().getName()+" to "+nextEdge.getDestination().getName());
                findPath(searchAllPathParam.getSource(), nextEdge, searchAllPathParam.getDestination(), tmpList, edgeList, typeMap, adjacencyEdgeMapForsource, visitedEdge, sourceStep, searchAllPathParam.getDirection(),VertexRoadMapForSource,true,vertexInStep);
                visitedEdge.remove((nextEdge));
                tmpList.pollLast();
            }
        }
        visitedEdge.clear();
        vertexInStep.clear();
        //再遍历终点所有边, 如果找到起点则忽略，如果没有找到起点则存点-路径（数组）
        for (MergeEdge nextEdge : nextEdgeListForDestinate) {
            //如果边类型包括
            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
                //添加在队尾
                tmpList.offerLast(nextEdge);
                //已访问加入
                visitedEdge.add(nextEdge);
//                log.info("findPath (destinate):"+nextEdge.getSource().getName()+" to "+nextEdge.getDestination().getName());
                findPath(searchAllPathParam.getDestination(), nextEdge, searchAllPathParam.getSource(), tmpList, edgeList, typeMap, adjacencyEdgeMapFordestinate, visitedEdge, destinateStep, searchAllPathParam.getDirection(),VertexRoadMapForDestinate,false,vertexInStep);
                visitedEdge.remove((nextEdge));
                tmpList.pollLast();
            }
        }

        //开始两端碰撞
        Set<Vertex> vertexSetForSource=VertexRoadMapForSource.keySet();
        Set<Vertex> vertexSetForDestinate=VertexRoadMapForDestinate.keySet();
        Collection<Vertex> intersectionSet=CollectionUtil.intersection(vertexSetForSource,vertexSetForDestinate);
        long roadNum=0;
        Iterator<Vertex> it=intersectionSet.iterator();
        while(it.hasNext()){
            Vertex vertexPoint=it.next();
//            log.info("the Vertex is:"+vertexPoint.getName());
            List<List<MergeEdge>>roadListForsource=VertexRoadMapForSource.get(vertexPoint);
            List<List<MergeEdge>>roadListFordestinate=VertexRoadMapForDestinate.get(vertexPoint);
//            log.info("the roadListForsource size is:"+roadListForsource.size()+"the roadListFordestinate size is"+roadListFordestinate.size());
            for(List<MergeEdge> sourceRoadList:roadListForsource){
                for (List<MergeEdge> destinateRoadList:roadListFordestinate)
                {
                    List<MergeEdge> road=new ArrayList<>(sourceRoadList);
                    road.addAll(destinateRoadList);
                    edgeList.add(road);
                    roadNum++;
                    if(roadNum>=searchAllPathParam.getTotalRoad()){
                        break;
                    }
                }
                if(roadNum>=searchAllPathParam.getTotalRoad()){
                    break;
                }
            }
            if(roadNum>=searchAllPathParam.getTotalRoad()){
                break;
            }
        }
        Date endTime=DateUtil.date();

        log.info("Search Path return result:"+edgeList.size()+" use time :"+ DateUtil.between(startTime,endTime, DateUnit.SECOND));
        return edgeList;
    }

    private void findPath(Vertex lastVertex, MergeEdge startEdge, Vertex destination, Deque<MergeEdge> tmpList, List<List<MergeEdge>> edgeList, Set<EdgeType> typeMap, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, Set<MergeEdge> visitedEdge, long maxStep, SearchPathDirection direction,HashMap<Vertex,List<List<MergeEdge>>> VertexRoadMap,boolean isSource,HashMap<Vertex,Long> vertexInStep) {

        Vertex destNode;
        //根据方向取出下个节点
        switch (direction) {
            case forward:
                destNode = startEdge.getDestination();
                break;

            case backward:
                destNode = startEdge.getSource();
                break;

            //无向
            default:
                destNode = startEdge.getSource().equals(lastVertex) ? startEdge.getDestination() : startEdge.getSource();

        }

        //超过最大步数返回
        if (tmpList.size() > maxStep) {
            return;
        }
        //找到目标节点，当前路径加入edgeList并返回
        if (destNode.equals(destination)) {
            if(isSource){    //双向查找的情况下，先查的起点，再查的终点，所以从终点出发直接找到起点的路劲应该忽略
                edgeList.add(new LinkedList<>(tmpList));
            }
            return;
        }else{
            List road= Arrays.asList(tmpList.toArray()); //获取当前点未知的路径
            if(VertexRoadMap.containsKey(destNode))
            {
                VertexRoadMap.get(destNode).add(road);//维护点拥有的路径集合
            }else{
                List<List<MergeEdge>> roadList=new ArrayList<>();
                roadList.add(road);
                VertexRoadMap.put(destNode,roadList);
            }
            //判断本路径走到该点时，步数是不是最小,路径步数大于最小值则停止迭代，否则替换该最小值
            if(vertexInStep.containsKey(destNode)){
                if(vertexInStep.get(destNode)>tmpList.size()){
                    vertexInStep.put(destNode, (long) tmpList.size());
                }else{
                    return;
                }
            }else{
                vertexInStep.put(destNode,(long) tmpList.size());
            }
        }
        //取出以destNode为起点的边
        Set<MergeEdge> nextEdgeList = adjacencyEdgeMap.get(destNode);
        if (CollectionUtil.isEmpty(nextEdgeList)) {
            return;
        }
        //递归入口
        for (MergeEdge nextEdge : nextEdgeList) {
            //如果边没有被访问过，且在搜索的type中，传入递归
            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
                tmpList.offerLast(nextEdge);
                visitedEdge.add(nextEdge);
//                log.info("findPath:"+nextEdge.getSource().getName()+" to "+nextEdge.getDestination().getName());
                findPath(destNode, nextEdge, destination, tmpList, edgeList, typeMap, adjacencyEdgeMap, visitedEdge, maxStep, direction,VertexRoadMap,isSource,vertexInStep);
                visitedEdge.remove((nextEdge));
                tmpList.pollLast();
            }
        }
    }



//    public List<List<MergeEdge>> searchAllPathold(SearchAllPathParam searchAllPathParam) {
//        //深度优先搜索
//        //定义返回列表
//        List<List<MergeEdge>> edgeList = new ArrayList<>();
//        Date startTime=DateUtil.date();
//        //判断入参是否符合条件
//        if (searchAllPathParam.getSource() == null || searchAllPathParam.getDestination() == null || searchAllPathParam.getSource() == searchAllPathParam.getDestination() || searchAllPathParam.getMaxStep() == 0) {
//            return edgeList;
//        }
//
//        Space space = SpaceContextHolder.get();
//
//        //定义不同路径方向的邻接表
//        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = new HashMap<>();
//
//        //下一步的边集
//        Set<MergeEdge> nextEdgeList;
//
//        //判断边是否被访问过
//        Set<MergeEdge> visitedEdge = new HashSet<>();
//
//        //将边类型储存方便查询，边类型存储到一个HashSet中
//        Set<EdgeType> typeMap = new HashSet<>(searchAllPathParam.getEdgeTypeList());
//
//        //存入临时路径，deque是双端队列
//        Deque<MergeEdge> tmpList = new LinkedList<>();
//
//        switch (searchAllPathParam.getDirection()) {
//            case forward:
//                adjacencyEdgeMap = space.getMergeEdgeAdjacencyTableMapByCard();
//                //判断是否有以source为起点的边
//                if (!adjacencyEdgeMap.containsKey(searchAllPathParam.getSource())) {
//                    return edgeList;
//                }
//
//                //取出以source为起点的边
//                nextEdgeList = adjacencyEdgeMap.get(searchAllPathParam.getSource());
//
//                break;
//
//            case backward:
//                adjacencyEdgeMap = space.getReverseMergeEdgeAdjacencyTableMapByCard();
//
//                if (!adjacencyEdgeMap.containsKey(searchAllPathParam.getSource())) {
//                    return edgeList;
//                }
//
//                //取出以source为起点的边
//                nextEdgeList = adjacencyEdgeMap.get(searchAllPathParam.getSource());
//
//                break;
//
//            //无向时合并Map
//            default:
//                adjacencyEdgeMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();
//
//                if (!adjacencyEdgeMap.containsKey(searchAllPathParam.getSource())) {
//                    return edgeList;
//                }
//
//                //取出以source为起点的边
//                nextEdgeList = adjacencyEdgeMap.get(searchAllPathParam.getSource());
//        }
//
//        if (CollectionUtil.isEmpty(nextEdgeList)) {
//            return edgeList;
//        }
//        //遍历所有边, 然后回溯
//        for (MergeEdge nextEdge : nextEdgeList) {
//            //如果边类型包括
//            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
//                //添加在队尾
//                tmpList.offerLast(nextEdge);
//                //已访问加入
//                visitedEdge.add(nextEdge);
////                log.info("findPath:"+nextEdge.getSource().getName()+" to "+nextEdge.getDestination().getName());
//                findPathold(searchAllPathParam.getSource(), nextEdge, searchAllPathParam.getDestination(), tmpList, edgeList, typeMap, adjacencyEdgeMap, visitedEdge, searchAllPathParam.getMaxStep(), searchAllPathParam.getDirection());
//                visitedEdge.remove((nextEdge));
//                tmpList.pollLast();
//            }
//        }
//        Date endTime=DateUtil.date();
//
//        log.info("Search Path return result:"+edgeList.size()+" use time :"+ DateUtil.between(startTime,endTime, DateUnit.SECOND));
//        return edgeList;
//    }
//
//    private void findPathold(Vertex lastVertex, MergeEdge startEdge, Vertex destination, Deque<MergeEdge> tmpList, List<List<MergeEdge>> edgeList, Set<EdgeType> typeMap, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, Set<MergeEdge> visitedEdge, long maxStep, SearchPathDirection direction) {
//
//        Vertex destNode;
//        //根据方向取出下个节点
//        switch (direction) {
//            case forward:
//                destNode = startEdge.getDestination();
//                break;
//
//            case backward:
//                destNode = startEdge.getSource();
//                break;
//
//            //无向
//            default:
//                destNode = startEdge.getSource().equals(lastVertex) ? startEdge.getDestination() : startEdge.getSource();
//
//        }
//
//        //超过最大步数返回
//        if (tmpList.size() > maxStep) {
//            return;
//        }
//        //找到目标节点，当前路径加入edgeList并返回
//        if (destNode.equals(destination)) {
//            edgeList.add(new LinkedList<>(tmpList));
//            return;
//        }
//        //不包含下个结点
//        if (!adjacencyEdgeMap.containsKey(destNode)) {
//            return;
//        }
//
//        //取出以destNode为起点的边
//        Set<MergeEdge> nextEdgeList = adjacencyEdgeMap.get(destNode);
//        if (CollectionUtil.isEmpty(nextEdgeList)) {
//            return;
//        }
//        //递归入口
//        for (MergeEdge nextEdge : nextEdgeList) {
//            //如果边没有被访问过，且在搜索的type中，传入递归
//            if (!visitedEdge.contains(nextEdge) && typeMap.contains(nextEdge.getType())) {
//                tmpList.offerLast(nextEdge);
//                visitedEdge.add(nextEdge);
////                log.info("findPath:"+nextEdge.getSource().getName()+" to "+nextEdge.getDestination().getName());
//                findPathold(destNode, nextEdge, destination, tmpList, edgeList, typeMap, adjacencyEdgeMap, visitedEdge, maxStep, direction);
//                visitedEdge.remove((nextEdge));
//                tmpList.pollLast();
//            }
//        }
//    }

    @Override
    public List<List<MergeEdge>> searchShortestPath(SearchShortestPathParam searchShortestPathParam) {
        //初始化
        SearchAllPathParam searchAllPathParam = new SearchAllPathParam();
        searchAllPathParam.setSource(searchShortestPathParam.getSource());
        searchAllPathParam.setDestination(searchShortestPathParam.getDestination());
        searchAllPathParam.setEdgeTypeList(searchShortestPathParam.getEdgeTypeList());

        searchAllPathParam.setDirection(searchShortestPathParam.getDirection());
        searchAllPathParam.setTotalRoad(searchShortestPathParam.getTotalRoad());
        long totalStep=1;
        searchAllPathParam.setMaxStep(totalStep);

        List<List<MergeEdge>> pathList = this.searchAllPath(searchAllPathParam);
        while(pathList.size()==0&&totalStep<searchShortestPathParam.getMaxStep()){
            totalStep++;
            searchAllPathParam.setMaxStep(totalStep);
            pathList=this.searchAllPath(searchAllPathParam);
        }
        if (pathList.size() == 0) {
            return null;
        }

//        List<MergeEdge> shortPath = pathList.get(0);
//
//        for (List<MergeEdge> edges : pathList) {
//            if (edges.size() > shortPath.size()) {
//                shortPath = edges;
//            }
//        }
        return pathList;
    }

    /**
     * 获取同类实体之间N步以内的关系
     *
     * @param searchAllPathParam
     * @return
     */
    @Override
    public List<MergeEdge> searchSameTagPath(SearchSameTagPathParam searchAllPathParam) {
        //获取所有节点并找出符合类型的点
        Set<MergeEdge> resultSet = new HashSet<>();  //保存结果集
        Space space = SpaceContextHolder.get();
        List<Vertex> vetexlist= new ArrayList<>();
        //如果参数中有实体列表则用该列表的实体，如果没有则使用所有实体
        if(CollectionUtil.isNotEmpty(searchAllPathParam.getVertexList()))
        {
            vetexlist=searchAllPathParam.getVertexList();
        }else{
            vetexlist=space.getVertexList();
        }
//        List<Vertex> vetexlist = space.getVertexList();
//        long walkingStep = searchAllPathParam.getMaxStep() / 2 + (searchAllPathParam.getMaxStep() % 2 == 0 ? 0 : 1);  //确定每个点向外括多少层
        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();
        for (Vertex v : vetexlist) {
            if (v.getTag().equals(searchAllPathParam.getTag())) {
                LinkedList<Vertex> visitedVertex = new LinkedList<>();
                LinkedList<MergeEdge> roadOfPrevious =new LinkedList<>();  //记录路径
                visitedVertex.offerLast(v);
//                log.info("begin to searchSameTagPath :"+v.getName());
                BFSSamTag(v, visitedVertex, adjacencyEdgeMap, searchAllPathParam.getMaxStep(), 1, roadOfPrevious, searchAllPathParam, resultSet);
            }
        }
        List<MergeEdge> resultList = new ArrayList<>(resultSet);
        return resultList;
    }

    /**迭代每个实体的路径，获取同类节点关联路径
     *
     * @param currentv                当前扩展的节点
     * @param visitedVertex           本次路径已经访问过的节点
     * @param adjacencyEdgeMap        本次路径已经访问过的节点
     * @param walkingStep             当前扩招到第几步了
     * @param roadOfPrevious        优化后的扩展步数
     * @param currentStep               上一个节点为止的路径
     * @param searchAllPathParam      用户参数
     * @param resultSet               结果集
     */
    private void BFSSamTag(Vertex currentv, LinkedList<Vertex> visitedVertex, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, long walkingStep, long currentStep, LinkedList<MergeEdge> roadOfPrevious, SearchSameTagPathParam searchAllPathParam, Set<MergeEdge> resultSet) {
        Set<MergeEdge> mergeEdgeSet = adjacencyEdgeMap.get(currentv);

        if(mergeEdgeSet == null) {
            return;
        }

        //针对V的每一条邻接线段进行迭代
        for (MergeEdge m : mergeEdgeSet) {
            Vertex oppositeV = m.getDestination().equals(currentv) ? m.getSource() : m.getDestination(); //找到对端节点
//            log.info("The  oppositeV is:"+oppositeV.getName());
            roadOfPrevious.offerLast(m);//将当前线段压入路径尾部
            //如果找到对端相同类型的点且不是起点则将路径加入结果集
            if (oppositeV.getTag().equals(searchAllPathParam.getTag())) {
                if(!visitedVertex.contains(oppositeV)||(visitedVertex.contains(oppositeV)&&roadOfPrevious.size()>2)){
//                    log.info("Find the road from:"+visitedVertex.get(0).getName()+" to:"+oppositeV.getName());
                    resultSet.addAll(roadOfPrevious);
                }
                roadOfPrevious.pollLast();
//                if(visitedVertex.size()>1){   //确保至少有一个起点
//                    visitedVertex.remove(oppositeV);
//                }
                continue;
            }
            if (visitedVertex.contains(oppositeV)) { //去掉回头路,以及找到目标类型实体则停止
                roadOfPrevious.pollLast();
//                visitedVertex.remove(oppositeV);
                continue;
            } else {
                visitedVertex.offerLast(oppositeV);
                currentStep++;
                if (currentStep <= walkingStep) {  //currentStep是从1开始的，所以需要<=
                    BFSSamTag(oppositeV, visitedVertex, adjacencyEdgeMap, walkingStep, currentStep, roadOfPrevious, searchAllPathParam, resultSet);
                }
                currentStep--;
                roadOfPrevious.pollLast();
                visitedVertex.pollLast();
            }

        }
    }

    /**
     * 获取同类实体之间N步以内的关系--优化算法
     *
     * @param searchAllPathParam
     * @return
     */
////    @Override
//    public List<MergeEdge> searchSameTagPath2(SearchSameTagPathParam searchAllPathParam) {
//        //获取所有节点并找出符合类型的点
//        Set<MergeEdge> resultSet = new HashSet<>();  //保存结果集
//        Space space = SpaceContextHolder.getThreadLocal();
//        List<Vertex> vetexlist = space.getVertexList();
//        List<Vertex> vetexlistForResult = space.getVertexList();  //纳入结果集的节点
//        Map<Vertex,LinkedList<MergeEdge>> roadOfPreviousForResult =new HashMap<>(); //纳入结果集的点/路径列表
//        Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap = ModelHelper.getAdjacencyEdgeMapWithoutDirection();
//        for (Vertex v : vetexlist) {
//            if (v.getTag().equals(searchAllPathParam.getTag())) {
//                List<Vertex> visitedVertex = new ArrayList<>();
//                LinkedList<MergeEdge> roadOfPrevious =new LinkedList<>();  //记录路径
//                visitedVertex.add(v);
//                log.info("begin to searchSameTagPath :"+v.getName());
//                BFSSamTag(v, visitedVertex, adjacencyEdgeMap, searchAllPathParam.getMaxStep(), 1, roadOfPrevious, searchAllPathParam, resultSet,vetexlistForResult,roadOfPreviousForResult);
//            }
//        }
//        List<MergeEdge> resultList = new ArrayList<>(resultSet);
//        return resultList;
//    }
//
//    private void BFSSamTag(Vertex currentv, List<Vertex> visitedVertex, Map<Vertex, Set<MergeEdge>> adjacencyEdgeMap, Long walkingStep, long currentStep, LinkedList<MergeEdge> roadOfPrevious, SearchSameTagPathParam searchAllPathParam, Set<MergeEdge> resultSet, List<Vertex> vetexlistForResult, Map<Vertex,LinkedList<MergeEdge>> roadOfPreviousForResult) {
//        //针对V的每一条邻接线段进行迭代
//        Set<MergeEdge> mergeEdgeSet = adjacencyEdgeMap.get(currentv);
//        for (MergeEdge m : mergeEdgeSet) {
//            Vertex oppositeV = m.getDestination().equals(currentv) ? m.getSource() : m.getDestination(); //找到对端节点
////            log.info("The  oppositeV is:"+oppositeV.getName());
//            LinkedList<MergeEdge> tmpList= new LinkedList<>();
//            tmpList.addAll(roadOfPrevious);  //记录加入当前线段之前的路径
//            //如果找到对端相同类型的点且不是起点则将路径加入结果集
//            if (oppositeV.getTag().equals(searchAllPathParam.getTag())) {
//                log.info("Find the road from:"+visitedVertex.get(0).getName()+" to:"+oppositeV.getName());
//                resultSet.addAll(roadOfPrevious);
//                resultSet.add(m);                 //完成当前路径的存入结果集
//                vetexlistForResult.add(currentv);                 //压入明星节点
//                roadOfPreviousForResult.put(currentv,tmpList);    //压入明星节点的路径
//                continue;
//            }
//            if(vetexlistForResult.contains(oppositeV)){   //明星节点{
//
//            }
//
//            if (visitedVertex.contains(oppositeV)) { //去掉回头路,以及找到目标类型实体则停止
//                continue;
//            } else {
//                visitedVertex.add(oppositeV);
//                currentStep++;
//                if (currentStep <= walkingStep) {  //currentStep是从1开始的，所以需要<=
//                    roadOfPrevious.offerLast(m);//将当前线段压入路径尾部
//                    BFSSamTag(oppositeV, visitedVertex, adjacencyEdgeMap, walkingStep, currentStep, roadOfPrevious, searchAllPathParam, resultSet);
//                    roadOfPrevious.pollLast();
//                }
//            }
//
//        }
//    }

}
